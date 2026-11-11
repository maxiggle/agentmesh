import argparse
import asyncio
import contextlib
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path

from ehor.cli import CliError, Commands, device_home, device_passphrase
from ehor.crypto.keyfile import KeyFileError
from ehor.identity import KeyHandle, KeyNotFoundError
from ehor.identity.base import PresenceSigner
from ehor.identity.secure_enclave import SecureEnclavePresenceSigner, SwiftHelper
from ehor.service.config import DeviceConfig
from ehor.service.device import check_passphrase, key_dir, tools_dir
from ehor.service.rpc import RpcClient, RpcError, default_endpoint
from ehor_near.client import TESTNET, NearClient, NearError, is_account_id
from ehor_near.config import NearConfig, NearConfigError
from ehor_near.limited_key import (
    ALLOWED_METHODS,
    DEFAULT_ALLOWANCE,
    LimitedKey,
    LimitedKeyError,
    LimitedKeyFile,
)
from ehor_near.pay import (
    Bounty,
    PayError,
    check_payable,
    fetch_bounty,
    registered_release_key,
    submit_release,
)
from ehor_near.payload import PayloadError, format_near, parse_near
from ehor_near.pynear import PyNearClient
from ehor_near.registration import RegistrationError, confirm_registration, registration_call
from ehor_near.release import ReleaseSignError, ReleaseTerms, sign_release
from ehor_near.wallet_page import WalletCall, WalletPage, WalletPageError

RELEASE_KEY_LABEL = "release"
LIMITED_KEY_FILE = "near-limited.key"
DEFAULT_CONTRACT = "ehor-escrow.testnet"
SETUP_TIMEOUT = 900.0
_NO_NEAR = "this device has no NEAR account; run `ehor near setup` first"
_NO_RELEASE_KEY = "this device has no release key; run `ehor near setup` first"
_MAC_ONLY = "releases need a Mac with a Secure Enclave in this version"
_ERRORS = (
    LimitedKeyError,
    NearConfigError,
    NearError,
    PayError,
    RegistrationError,
    ReleaseSignError,
    WalletPageError,
)

Run = Callable[[argparse.Namespace], int]


def register(commands: Commands) -> None:
    near = commands.add_parser("near", help="pay for requests with NEAR testnet escrow")
    near_commands = near.add_subparsers(required=True, metavar="command")
    setup = near_commands.add_parser(
        "setup", help="connect your NEAR wallet and register this Mac's Touch ID release key"
    )
    setup.add_argument("--contract", default=DEFAULT_CONTRACT, help="escrow contract account")
    setup.add_argument(
        "--deposit", default="1", help="NEAR to deposit as your bounty budget if not registered"
    )
    setup.add_argument(
        "--max-per-bounty", default="0.5", help="largest bounty in NEAR, if not registered"
    )
    setup.set_defaults(run=_reporting(_near_setup))

    pay = commands.add_parser("pay", help="review a done bounty and pay it with Touch ID")
    pay.add_argument("bounty", type=int, help="bounty id on the escrow contract")
    pay.set_defaults(run=_reporting(_pay))


def _reporting(run: Run) -> Run:
    def report(args: argparse.Namespace) -> int:
        try:
            return run(args)
        except _ERRORS as exc:
            raise CliError(str(exc)) from exc

    return report


def _pay(args: argparse.Namespace) -> int:
    home = device_home(args)
    near = NearConfig.load(home)
    if near is None:
        raise CliError(_NO_NEAR)
    signer = _release_signer(home)
    handle = KeyHandle(SecureEnclavePresenceSigner.backend, RELEASE_KEY_LABEL)
    try:
        release_key = signer.public_key(handle)
    except KeyNotFoundError as exc:
        raise CliError(_NO_RELEASE_KEY) from exc
    client = _connect_near(near, _limited_key(home).near_secret())
    return asyncio.run(
        _pay_bounty(
            home,
            near,
            client=client,
            signer=signer,
            handle=handle,
            release_key=release_key,
            bounty_id=args.bounty,
        )
    )


async def _pay_bounty(
    home: Path,
    config: NearConfig,
    *,
    client: "contextlib.AbstractAsyncContextManager[NearClient]",
    signer: PresenceSigner,
    handle: KeyHandle,
    release_key: bytes,
    bounty_id: int,
) -> int:
    contract = config.contract_id
    async with client as near:
        bounty = await fetch_bounty(near, contract, bounty_id)
        claimant = check_payable(bounty, config.account_id)
        if await registered_release_key(near, contract, config.account_id) != release_key:
            raise CliError(
                f"{contract} has a different release key for {config.account_id}; "
                "run `ehor near setup` to register this device's key"
            )
        amount = format_near(bounty.amount_yocto)
        sys.stdout.write(f"{bounty.describe()}\n\n{await _linked_message(home, bounty)}\n\n")
        if not _confirm(f"Pay {amount} NEAR to {claimant}? [y/N] "):
            sys.stdout.write("not paid\n")
            return 1
        terms = ReleaseTerms(config.network, contract, bounty.id, claimant, bounty.amount_yocto)
        sys.stdout.write("approve with Touch ID or your login password\n")
        signature = sign_release(signer, handle, terms)
        await submit_release(near, contract, bounty.id, signature)
    sys.stdout.write(f"paid {amount} NEAR to {claimant} for bounty {bounty.id}\n")
    return 0


async def _linked_message(home: Path, bounty: Bounty) -> str:
    try:
        return await RpcClient(default_endpoint(home)).call("read", {"id": bounty.message_id})
    except RpcError as exc:
        return f"linked message {bounty.message_id} not shown: {exc}"


def _confirm(prompt: str) -> bool:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _limited_key(home: Path) -> LimitedKey:
    path = key_dir(home) / LIMITED_KEY_FILE
    if not path.exists():
        raise CliError("this device has no NEAR key; run `ehor near setup` first")
    return LimitedKeyFile(path, device_passphrase()).load()


def _release_signer(home: Path) -> PresenceSigner:
    if sys.platform != "darwin":
        raise CliError(_MAC_ONLY)
    return SecureEnclavePresenceSigner(key_dir(home), SwiftHelper(tools_dir(home)))


def _connect_near(
    config: NearConfig, private_key: str
) -> "contextlib.AbstractAsyncContextManager[NearClient]":
    return PyNearClient.connect(config.account_id, private_key, config.rpc_url)


def _near_setup(args: argparse.Namespace) -> int:
    home = device_home(args)
    DeviceConfig.load(home)
    contract: str = args.contract
    if not is_account_id(contract):
        raise CliError(f"not a NEAR account id: {contract!r}")
    try:
        deposit = parse_near(args.deposit)
        max_per_bounty = parse_near(args.max_per_bounty)
    except PayloadError as exc:
        raise CliError(str(exc)) from exc
    signer = _release_signer(home)
    handle = KeyHandle(SecureEnclavePresenceSigner.backend, RELEASE_KEY_LABEL)
    try:
        release_key = signer.public_key(handle)
    except KeyNotFoundError:
        release_key = signer.public_key(signer.create_key(RELEASE_KEY_LABEL))
        sys.stdout.write("created this Mac's Touch ID release key\n")
    passphrase = device_passphrase()
    try:
        check_passphrase(home, passphrase)
    except KeyFileError as exc:
        raise CliError("that passphrase does not unlock this device") from exc
    limited = LimitedKeyFile(key_dir(home) / LIMITED_KEY_FILE, passphrase).load_or_create()
    sys.stdout.write(
        f"Your browser will open a page where your NEAR wallet asks for up to two approvals:\n"
        f"  1. add a limited key for {contract}, with a gas allowance of "
        f"{format_near(DEFAULT_ALLOWANCE)} NEAR; it cannot move NEAR\n"
        f"  2. if your account is new to {contract}, register with a deposit of "
        f"{format_near(deposit)} NEAR and a limit of {format_near(max_per_bounty)} NEAR per "
        "bounty; if it has another release key, replace it with this Mac's\n"
    )
    account = asyncio.run(
        _connect_wallet(
            contract,
            limited,
            release_key,
            deposit_yocto=deposit,
            max_per_bounty_yocto=max_per_bounty,
        )
    )
    NearConfig(account, contract).save(home)
    sys.stdout.write(
        f"{account} is set up on {contract}. This Mac's release key approves payouts with Touch ID,"
        " and `ehor pay` sends them with the limited key.\n"
    )
    return 0


async def _connect_wallet(
    contract: str,
    limited: LimitedKey,
    release_key: bytes,
    *,
    deposit_yocto: int,
    max_per_bounty_yocto: int,
) -> str:
    async def plan(account: str) -> WalletCall | None:
        async with _connect_near(NearConfig(account, contract), limited.near_secret()) as near:
            return await registration_call(
                near,
                contract,
                account,
                release_key,
                max_per_bounty_yocto=max_per_bounty_yocto,
                deposit_yocto=deposit_yocto,
            )

    with WalletPage(
        network=TESTNET,
        contract=contract,
        public_key=limited.public_key,
        methods=ALLOWED_METHODS,
        allowance_yocto=DEFAULT_ALLOWANCE,
        planner=plan,
    ) as page:
        sys.stdout.write(f"If the page does not open, visit {page.url}\n")
        sys.stdout.flush()
        _open_browser(page.url)
        link = await page.wait(SETUP_TIMEOUT)
    sys.stdout.write(f"{link.account_id} approved in the wallet; checking the chain\n")
    async with _connect_near(NearConfig(link.account_id, contract), limited.near_secret()) as near:
        try:
            await confirm_registration(
                near,
                contract,
                link.account_id,
                limited_public_key=limited.public_key,
                release_key=release_key,
                max_allowance_yocto=DEFAULT_ALLOWANCE,
            )
        except LimitedKeyError as exc:
            raise CliError(
                f"{exc}. Remove the key {limited.public_key} from {link.account_id} in your "
                "wallet if it is there, then run `ehor near setup` again."
            ) from exc
    return link.account_id


def _open_browser(url: str) -> None:
    webbrowser.open(url)
