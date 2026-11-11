import argparse
import asyncio
import contextlib
import getpass
import logging
import os
import signal
import sys
import webbrowser
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path

from ehor.agents import (
    Agent,
    SetupError,
    command_names,
    default_target,
    install,
    instructions,
)
from ehor.crypto.keyfile import KeyFileError
from ehor.escrow.client import TESTNET, NearClient, NearError, is_account_id
from ehor.escrow.config import NearConfig, NearConfigError
from ehor.escrow.limited_key import (
    ALLOWED_METHODS,
    DEFAULT_ALLOWANCE,
    LimitedKey,
    LimitedKeyError,
    LimitedKeyFile,
)
from ehor.escrow.pay import (
    Bounty,
    PayError,
    check_payable,
    fetch_bounty,
    registered_release_key,
    submit_release,
)
from ehor.escrow.payload import PayloadError, format_near, parse_near
from ehor.escrow.pynear import PyNearClient
from ehor.escrow.registration import RegistrationError, confirm_registration, registration_call
from ehor.escrow.release import ReleaseSignError, ReleaseTerms, sign_release
from ehor.escrow.wallet_page import WalletCall, WalletPage, WalletPageError
from ehor.identity import HardwareUnavailableError, IdentityError, KeyHandle, KeyNotFoundError
from ehor.identity.backends import Backend
from ehor.identity.base import PresenceSigner
from ehor.identity.secure_enclave import SecureEnclavePresenceSigner, SwiftHelper
from ehor.identity.security_key import Authenticator, SecurityKeySigner
from ehor.log.entry import EntryContent, EntryFormatError
from ehor.log.names import parse_address
from ehor.log.roster import Roster, RosterError
from ehor.mcp.server import build_server
from ehor.relay.server import Relay, RelayError
from ehor.service.alerts import AlertWatcher
from ehor.service.config import ConfigError, DeviceConfig, default_home
from ehor.service.daemon import DEFAULT_SYNC_INTERVAL, Daemon, Syncer
from ehor.service.device import (
    Device,
    check_passphrase,
    key_dir,
    security_key_signer,
    tools_dir,
)
from ehor.service.direct_sync import DirectSync
from ehor.service.invite import Invite, InviteError
from ehor.service.iroh_key import IrohKeyError
from ehor.service.notify import (
    Alert,
    Notifier,
    NotifierGroup,
    NotifyError,
    NtfyNotifier,
    PhoneTarget,
    default_notifier,
)
from ehor.service.peer_sync import DEFAULT_PEER_INTERVAL, PeerSync
from ehor.service.relay_policy import RelayGate, devices_to_reach
from ehor.service.rpc import RpcClient, RpcError, default_endpoint
from ehor.transport.iroh import IrohMode, IrohTransport
from ehor.transport.lan import ZeroconfDiscovery
from ehor.transport.relays import RelayDirectory

PASSPHRASE_ENV = "EHOR_PASSPHRASE"  # noqa: S105 - env var name, not a secret
DEFAULT_RELAY_PORT = 7447
AUTO_KEY = "auto"
KEY_CHOICES = [AUTO_KEY, *(backend.value for backend in Backend)]
_KEY_HELP = "where the signing key lives; auto uses a hardware key if this machine has one"
_SECURITY_KEY_HELP = "label of the enrolled security key to sign with, if there are several"
_FIDO2_EXTRA = "security keys need the fido2 extra: pip install 'ehor[fido2]'"
_NO_NOTIFIER = "this system has no notification command; on Linux, install notify-send"
_PHONE_HELP = (
    "Install the ntfy app (https://ntfy.sh) and subscribe to that URL. Keep it private: anyone"
    " with it can read the sender and summary of your new messages, and so can its server."
)
RELEASE_KEY_LABEL = "release"
LIMITED_KEY_FILE = "near-limited.key"
DEFAULT_CONTRACT = "ehor-escrow.testnet"
SETUP_TIMEOUT = 900.0
_NO_NEAR = "this device has no NEAR account; run `ehor near setup` first"
_NO_RELEASE_KEY = "this device has no release key; run `ehor near setup` first"
_MAC_ONLY = "releases need a Mac with a Secure Enclave in this version"
_SKIP_PROMPT = f"Set {PASSPHRASE_ENV} to skip this prompt."
_NEW_DEVICE_PROMPT = (
    "ehor encrypts this device's keys with a passphrase. You'll enter it each time you run"
    " `ehor serve`.\nIf you forget it, this device can't read its messages and you'll need to"
    " set it up again."
)
_DEVICE_PROMPT = (
    "Enter the passphrase you chose at `ehor init` or `ehor join` to unlock this device's keys."
    f" {_SKIP_PROMPT}"
)
_NEW_RELAY_PROMPT = (
    "ehor encrypts this relay's signing key with a passphrase. You'll enter it each time you run"
    " `ehor relay serve`."
)
_RELAY_PROMPT = (
    "Enter the passphrase you chose at `ehor relay init` to unlock this relay's key."
    f" {_SKIP_PROMPT}"
)


class CliError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.run(args))
    except (
        CliError,
        ConfigError,
        IdentityError,
        IrohKeyError,
        LimitedKeyError,
        NearConfigError,
        NearError,
        PayError,
        RelayError,
        RegistrationError,
        ReleaseSignError,
        RpcError,
        SetupError,
        WalletPageError,
    ) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ehor", description="Signed messaging between AI agents.")
    parser.add_argument("--home", type=Path, default=None, help="device folder")
    commands = parser.add_subparsers(required=True, metavar="command")

    init = commands.add_parser("init", help="start a new team on this device")
    init.add_argument("--handle", required=True, help="your short name, e.g. gef")
    init.add_argument("--team-name", default="", help="display name for the team")
    init.add_argument("--exchange", type=Path, help="shared folder for swapping entries")
    init.add_argument("--key", choices=KEY_CHOICES, default=AUTO_KEY, help=_KEY_HELP)
    init.set_defaults(run=_init)

    invite = commands.add_parser("invite", help="print this team's invite code")
    invite.add_argument("--relay", help="host:port of a relay to put in the code")
    invite.set_defaults(run=_invite)

    join = commands.add_parser("join", help="join a team from an invite code")
    join.add_argument("code", help="invite code from a teammate")
    join.add_argument("--handle", required=True, help="your short name, e.g. tobi")
    join.add_argument("--exchange", type=Path, help="shared folder for swapping entries")
    join.add_argument("--key", choices=KEY_CHOICES, default=AUTO_KEY, help=_KEY_HELP)
    join.set_defaults(run=_join)

    _add_roster_commands(commands)
    _add_relay_commands(commands)

    setup = commands.add_parser(
        "setup", help="install ehor's slash commands for Claude or opencode, or skills for Codex"
    )
    setup.add_argument("agent", choices=[agent.value for agent in Agent])
    setup.add_argument("--dir", type=Path, help="install here instead of your user folder")
    setup.add_argument("--force", action="store_true", help="replace files you have edited")
    setup.add_argument(
        "--instructions",
        action="store_true",
        help="print the snippet for a project's CLAUDE.md or AGENTS.md instead of installing",
    )
    setup.set_defaults(run=_setup)

    _add_near_commands(commands)

    pay = commands.add_parser("pay", help="review a done bounty and pay it with Touch ID")
    pay.add_argument("bounty", type=int, help="bounty id on the escrow contract")
    pay.set_defaults(run=_pay)

    serve = commands.add_parser("serve", help="run the background service")
    serve.add_argument("--interval", type=float, default=DEFAULT_SYNC_INTERVAL)
    serve.add_argument("--no-lan", action="store_true", help="do not sync over the local network")
    serve.add_argument("--no-p2p", action="store_true", help="do not connect to teammates directly")
    serve.add_argument("--lan-interval", type=float, default=DEFAULT_PEER_INTERVAL)
    serve.set_defaults(run=_serve)

    notify = commands.add_parser("notify", help="get notified about new messages")
    notify.add_argument(
        "action",
        nargs="?",
        choices=["on", "off", "phone", "test"],
        help="on or off for the desktop, phone to push to a phone; omit to show the settings",
    )
    notify.add_argument("target", nargs="?", help="for phone: an ntfy topic URL, or off")
    notify.set_defaults(run=_notify)

    commands.add_parser(
        "mcp", help="run the MCP server for Claude, Codex or another agent"
    ).set_defaults(run=_mcp)
    for tool in ("digest", "inbox", "team"):
        commands.add_parser(tool, help=f"show {tool}").set_defaults(run=_tool, tool=tool)
    return parser


def _add_roster_commands(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    roster = commands.add_parser("roster", help="show or change the team roster")
    roster.set_defaults(run=_roster_show)
    roster_commands = roster.add_subparsers(metavar="action")
    add = roster_commands.add_parser("add", help="add a member or a device (admins only)")
    add.add_argument("handle")
    add.add_argument("key", help="device key printed by `ehor join`")
    add.add_argument("--role", action="append", default=[], help="a role such as @mobile")
    add.add_argument("--channel", action="append", default=[], help="a channel such as #mobile")
    add.add_argument("--admin", action="store_true", help="allow this member to change the roster")
    add.add_argument("--label", default="", help="device name shown in the roster")
    add.add_argument(
        "--hardware", action="store_true", help="the key is in hardware, as `join` reported"
    )
    add.set_defaults(run=_roster_add)
    remove = roster_commands.add_parser("remove", help="remove a member (admins only)")
    remove.add_argument("handle")
    remove.set_defaults(run=_roster_remove)
    relay_add = roster_commands.add_parser("relay-add", help="add a relay (admins only)")
    relay_add.add_argument("address", help="host:port the relay listens on")
    relay_add.add_argument("key", help="relay key printed by `ehor relay init`")
    relay_add.set_defaults(run=_roster_relay_add)
    relay_remove = roster_commands.add_parser("relay-remove", help="remove a relay (admins only)")
    relay_remove.add_argument("address")
    relay_remove.set_defaults(run=_roster_relay_remove)
    enroll = roster_commands.add_parser(
        "enroll-key",
        help="sign your roster changes with a FIDO2 security key from now on (admins only)",
    )
    enroll.add_argument("--label", default="security-key", help="name for this key")
    enroll.set_defaults(run=_roster_enroll_key)
    for command in (add, remove, relay_add, relay_remove, enroll):
        command.add_argument("--security-key", metavar="LABEL", help=_SECURITY_KEY_HELP)


def _add_near_commands(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
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
    setup.set_defaults(run=_near_setup)


def _add_relay_commands(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    relay = commands.add_parser("relay", help="set up or run a relay for a team")
    relay_commands = relay.add_subparsers(required=True, metavar="action")
    relay_init = relay_commands.add_parser("init", help="create a relay from an invite code")
    relay_init.add_argument("--invite", required=True, help="invite code from a team admin")
    relay_init.set_defaults(run=_relay_init)
    relay_serve = relay_commands.add_parser("serve", help="run the relay")
    relay_serve.add_argument("--listen", default=f"0.0.0.0:{DEFAULT_RELAY_PORT}", help="host:port")
    relay_serve.set_defaults(run=_relay_serve)


def _home(args: argparse.Namespace) -> Path:
    home: Path | None = args.home
    return home if home is not None else default_home()


def _new_passphrase(explanation: str) -> bytes:
    from_env = os.environ.get(PASSPHRASE_ENV)
    if from_env:
        return from_env.encode()
    sys.stderr.write(f"{explanation}\n")
    passphrase = getpass.getpass("Choose a passphrase: ")
    if not passphrase:
        raise CliError("the passphrase can't be empty; run the command again")
    if getpass.getpass("Type it again to confirm: ") != passphrase:
        raise CliError("the passphrases didn't match; run the command again")
    return passphrase.encode()


def _existing_passphrase(explanation: str) -> bytes:
    from_env = os.environ.get(PASSPHRASE_ENV)
    if from_env:
        return from_env.encode()
    sys.stderr.write(f"{explanation}\n")
    passphrase = getpass.getpass("Passphrase: ")
    if not passphrase:
        raise CliError("the passphrase can't be empty")
    return passphrase.encode()


def _init(args: argparse.Namespace) -> int:
    home = _home(args)
    Device.check_setup(home, args.handle, args.team_name)
    _discard_unfinished_setup(home)
    exchange = None if args.exchange is None else args.exchange.resolve()
    with Device.create_team(
        home,
        args.handle,
        _new_passphrase(_NEW_DEVICE_PROMPT),
        team_name=args.team_name,
        exchange=exchange,
        key_backend=_key_backend(args),
    ) as device:
        sys.stdout.write(
            f"created team with {args.handle} as admin in {home}\n"
            f"{_describe_key(device.config.key_backend)}\n"
            f"invite  {device.invite.encode()}\n"
            "next: run `ehor serve`, then add `ehor mcp` to Claude or Codex\n"
        )
    return 0


def _discard_unfinished_setup(home: Path) -> None:
    if Device.discard_unfinished_setup(home):
        sys.stderr.write("Removed files left by an earlier setup that didn't finish.\n")


def _setup(args: argparse.Namespace) -> int:
    agent = Agent(args.agent)
    if args.instructions:
        sys.stdout.write(instructions(agent))
        return 0
    target: Path = args.dir if args.dir is not None else default_target(agent, Path.home())
    result = install(agent, target, force=args.force)
    names = ", ".join(command_names(agent))
    state = "installed" if result.written else "already up to date:"
    sys.stdout.write(f"{state} {names} in {target}\n")
    sys.stdout.write("".join(f"  wrote {path}\n" for path in result.written))
    sys.stdout.write(_setup_next_steps(agent, args.home))
    return 0


def _setup_next_steps(agent: Agent, home: Path | None) -> str:
    serve = "ehor mcp" if home is None else f"ehor --home {home} mcp"
    if agent is Agent.CLAUDE:
        scope = "--scope user " if home is None else ""
        add = f"claude mcp add {scope}ehor -- {serve}"
        app, snippet = "Claude Code", "CLAUDE.md"
    elif agent is Agent.CODEX:
        add = f"codex mcp add ehor -- {serve}"
        app, snippet = "Codex", "AGENTS.md"
    else:
        add = f"run `opencode mcp add` and add a local server named ehor that runs `{serve}`"
        app, snippet = "opencode", "AGENTS.md"
    return (
        "next:\n"
        f"  connect ehor to {app}, if you haven't:  {add}\n"
        f"  restart {app} to load them\n"
        f"  optional, to check your inbox at the start of each session:\n"
        f"    ehor setup {agent.value} --instructions >> {snippet}\n"
    )


def _invite(args: argparse.Namespace) -> int:
    home = _home(args)
    config = DeviceConfig.load(home)
    try:
        current = Invite.decode(asyncio.run(RpcClient(default_endpoint(home)).call("invite")))
    except RpcError:
        current = Invite(config.team, config.genesis, config.relay, Device.stored_endpoint_id(home))
    try:
        code = Invite(
            current.team, current.genesis, args.relay or current.relay, current.iroh
        ).encode()
    except InviteError as exc:
        raise CliError(str(exc)) from exc
    sys.stdout.write(f"{code}\nshare this with a teammate; they run `ehor join {code}`\n")
    return 0


def _join(args: argparse.Namespace) -> int:
    invite = _decode_invite(args.code)
    home = _home(args)
    Device.check_setup(home, args.handle)
    _discard_unfinished_setup(home)
    exchange = None if args.exchange is None else args.exchange.resolve()
    with Device.join(
        home,
        invite,
        args.handle,
        _new_passphrase(_NEW_DEVICE_PROMPT),
        exchange=exchange,
        key_backend=_key_backend(args),
    ) as device:
        hardware = " --hardware" if device.config.key_backend.hardware else ""
        sys.stdout.write(
            f"joined as {args.handle} in {home}\n"
            f"{_describe_key(device.config.key_backend)}\n"
            "ask an admin to run:\n"
            f"  ehor roster add {args.handle} {device.author.hex()}{hardware}\n"
        )
    return 0


def _decode_invite(code: str) -> Invite:
    try:
        return Invite.decode(code)
    except InviteError as exc:
        raise CliError(str(exc)) from exc


def _key_backend(args: argparse.Namespace) -> Backend | None:
    return None if args.key == AUTO_KEY else Backend(args.key)


def _describe_key(backend: Backend) -> str:
    if backend.hardware:
        return f"signing key  {backend} (hardware, cannot be copied off this machine)"
    return f"signing key  {backend} (passphrase-protected file)"


def _roster_show(args: argparse.Namespace) -> int:
    return _call(args, "roster.show", {})


def _roster_add(args: argparse.Namespace) -> int:
    params: dict[str, object] = {
        "handle": args.handle,
        "key": args.key,
        "roles": args.role,
        "channels": args.channel,
        "admin": args.admin,
        "label": args.label,
        "hardware": args.hardware,
    }
    return _change_roster(args, "roster.add", params)


def _roster_remove(args: argparse.Namespace) -> int:
    return _change_roster(args, "roster.remove", {"handle": args.handle})


def _roster_relay_add(args: argparse.Namespace) -> int:
    return _change_roster(args, "roster.relay_add", {"address": args.address, "key": args.key})


def _roster_relay_remove(args: argparse.Namespace) -> int:
    return _change_roster(args, "roster.relay_remove", {"address": args.address})


def _roster_enroll_key(args: argparse.Namespace) -> int:
    home = _home(args)
    keys = security_key_signer(home, _connect_security_key)
    enrolled = _enrolled_key(keys, args.security_key)
    sys.stderr.write("touch the new security key to create its ehor credential\n")
    handle = keys.create_key(args.label)
    params: dict[str, object] = {"key": keys.public_key(handle).hex(), "label": args.label}
    try:
        if enrolled is None:
            result = _rpc(home, "roster.enroll", params)
        else:
            _ask(
                f"now plug in only your enrolled security key {enrolled.key_id!r}, then press Enter"
            )
            signer = security_key_signer(home, _connect_security_key)
            result = _sign_with_security_key(home, signer, enrolled, "roster.enroll", params)
    except BaseException:
        keys.forget(handle)
        raise
    sys.stdout.write(f"{result}\nroster changes by you now need a touch of {args.label!r}\n")
    return 0


def _change_roster(args: argparse.Namespace, method: str, params: dict[str, object]) -> int:
    """Roster changes go through an enrolled security key when this device has one."""
    home = _home(args)
    keys = security_key_signer(home, _connect_security_key)
    enrolled = _enrolled_key(keys, args.security_key)
    if enrolled is None:
        result = _rpc(home, method, params)
    else:
        result = _sign_with_security_key(home, keys, enrolled, method, params)
    sys.stdout.write(result + "\n")
    return 0


def _enrolled_key(keys: SecurityKeySigner, label: str | None) -> KeyHandle | None:
    labels = keys.labels()
    if label is not None:
        if label not in labels:
            raise CliError(
                f"no security key named {label!r}; enrolled: {', '.join(labels) or 'none'}"
            )
        return KeyHandle(SecurityKeySigner.backend, label)
    if not labels:
        return None
    if len(labels) > 1:
        raise CliError(f"pick a security key with --security-key: {', '.join(labels)}")
    return KeyHandle(SecurityKeySigner.backend, labels[0])


def _sign_with_security_key(
    home: Path, keys: SecurityKeySigner, handle: KeyHandle, method: str, params: dict[str, object]
) -> str:
    """Have the service draft the change, sign it with a touch here, and hand it back."""
    draft = _rpc(home, method, {**params, "security_key": keys.public_key(handle).hex()})
    try:
        content = EntryContent.decode(bytes.fromhex(draft))
        version = Roster.decode(content.body).version
    except (ValueError, EntryFormatError, RosterError) as exc:
        raise CliError(f"the service sent an unusable draft: {exc}") from exc
    sys.stderr.write(f"touch your security key {handle.key_id!r} to sign roster v{version}\n")
    assertion = keys.sign_assertion(handle, content.signing_input)
    submit: dict[str, object] = {
        "content": draft,
        "auth_data": assertion.auth_data.hex(),
        "signature": assertion.signature.hex(),
    }
    return _rpc(home, "roster.submit", submit)


def _connect_security_key() -> Authenticator:
    try:
        from ehor.identity.ctap import Ctap2Authenticator  # noqa: PLC0415 - fido2 is an optional extra
    except ImportError as exc:
        raise HardwareUnavailableError(_FIDO2_EXTRA) from exc
    return Ctap2Authenticator.connect(pin=lambda: getpass.getpass("Security key PIN: "))


def _ask(prompt: str) -> None:
    sys.stderr.write(f"{prompt}\n")
    sys.stdin.readline()


def _relay_home(args: argparse.Namespace) -> Path:
    home: Path | None = args.home
    return home if home is not None else Path.home() / ".ehor-relay"


def _relay_init(args: argparse.Namespace) -> int:
    invite = _decode_invite(args.invite)
    home = _relay_home(args)
    with Relay.create(
        home, invite.team, invite.genesis, _new_passphrase(_NEW_RELAY_PROMPT)
    ) as relay:
        sys.stdout.write(
            f"created relay in {home}\n"
            "run it with `ehor relay serve`, then ask an admin to run:\n"
            f"  ehor roster relay-add <public-host>:{DEFAULT_RELAY_PORT} {relay.key.hex()}\n"
        )
    return 0


def _relay_serve(args: argparse.Namespace) -> int:
    parsed = parse_address(args.listen)
    if parsed is None:
        raise CliError(f"--listen must be host:port, got {args.listen!r}")
    _log_to_stderr()
    home = _relay_home(args)
    with Relay.open(home, _existing_passphrase(_RELAY_PROMPT)) as relay:
        sys.stderr.write(f"ehor relay {relay.key.hex()} listening on {args.listen}\n")
        host, port = parsed
        asyncio.run(_run_until_signal(lambda stop: relay.serve(host, port, stop)))
    return 0


def _call(args: argparse.Namespace, method: str, params: dict[str, object]) -> int:
    sys.stdout.write(_rpc(_home(args), method, params) + "\n")
    return 0


def _rpc(home: Path, method: str, params: dict[str, object]) -> str:
    return asyncio.run(RpcClient(default_endpoint(home)).call(method, params))


def _pay(args: argparse.Namespace) -> int:
    home = _home(args)
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
    return LimitedKeyFile(path, _existing_passphrase(_DEVICE_PROMPT)).load()


def _release_signer(home: Path) -> PresenceSigner:
    if sys.platform != "darwin":
        raise CliError(_MAC_ONLY)
    return SecureEnclavePresenceSigner(key_dir(home), SwiftHelper(tools_dir(home)))


def _connect_near(
    config: NearConfig, private_key: str
) -> "contextlib.AbstractAsyncContextManager[NearClient]":
    return PyNearClient.connect(config.account_id, private_key, config.rpc_url)


def _near_setup(args: argparse.Namespace) -> int:
    home = _home(args)
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
    passphrase = _existing_passphrase(_DEVICE_PROMPT)
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


def _serve(args: argparse.Namespace) -> int:
    _log_to_stderr()
    home = _home(args)
    with Device.open(home, _existing_passphrase(_DEVICE_PROMPT)) as device:
        sys.stderr.write(f"ehor serving {device.config.handle} from {home}\n")
        direct = None if args.no_p2p else _direct_sync(device)
        peers: list[Syncer] = [_relay_sync(device, args.lan_interval, direct)]
        if not args.no_lan:
            peers.append(_lan_sync(device, args.lan_interval))
        if direct is not None:
            peers.append(direct)
        daemon = Daemon(device, default_endpoint(home), args.interval, peers, _watcher(device))
        asyncio.run(_run_until_signal(daemon.run))
    return 0


def _watcher(device: Device) -> AlertWatcher | None:
    if device.config.notifications and default_notifier() is None:
        logging.getLogger(__name__).info("desktop notifications are off: %s", _NO_NOTIFIER)
    notifiers = _notifiers(device.config)
    return None if not notifiers else AlertWatcher(device.alert_feed, NotifierGroup(notifiers))


def _notifiers(config: DeviceConfig) -> list[Notifier]:
    desktop = default_notifier() if config.notifications else None
    phone = None if config.phone is None else NtfyNotifier(PhoneTarget(config.phone))
    return [notifier for notifier in (desktop, phone) if notifier is not None]


def _notify(args: argparse.Namespace) -> int:
    home = _home(args)
    config = DeviceConfig.load(home)
    if args.target is not None and args.action != "phone":
        raise CliError("only `ehor notify phone` takes a URL")
    if args.action == "test":
        _show_test_notification(config)
        return 0
    if args.action == "phone":
        config = _set_phone(config, args.target)
    elif args.action is not None:
        config = replace(config, notifications=args.action == "on")
    if args.action is not None:
        config.save(home)
    sys.stdout.write(_describe_notifications(config) + "\n")
    if args.action == "phone" and config.phone is not None:
        sys.stdout.write(_PHONE_HELP + "\n")
    if args.action is not None:
        sys.stdout.write("restart `ehor serve` to apply this\n")
    return 0


def _set_phone(config: DeviceConfig, target: str | None) -> DeviceConfig:
    if target == "off":
        return replace(config, phone=None)
    if target is None and config.phone is not None:
        return config
    if target is None:
        return replace(config, phone=PhoneTarget.generate().url)
    try:
        return replace(config, phone=PhoneTarget(target).url)
    except NotifyError as exc:
        raise CliError(str(exc)) from exc


def _describe_notifications(config: DeviceConfig) -> str:
    if not config.notifications:
        desktop = "off"
    elif default_notifier() is None:
        desktop = f"on, but {_NO_NOTIFIER}"
    else:
        desktop = "on"
    phone = "off" if config.phone is None else f"sent to {config.phone}"
    return f"desktop notifications: {desktop}\nphone notifications: {phone}"


def _show_test_notification(config: DeviceConfig) -> None:
    notifiers = _notifiers(config)
    if not notifiers:
        raise CliError(
            _NO_NOTIFIER if config.notifications else "notifications are off; see `ehor notify`"
        )
    try:
        NotifierGroup(notifiers).notify(Alert("test", "notifications work"))
    except NotifyError as exc:
        raise CliError(str(exc)) from exc


def _relay_sync(device: Device, interval: float, direct: DirectSync | None) -> PeerSync:
    directory = RelayDirectory(device.relay_addresses)
    gate = RelayGate(
        lambda: direct is not None and direct.running,
        lambda: None if direct is None else direct.last_round,
        lambda: devices_to_reach(device.roster.current, device.author),
        lambda: frozenset(key for key, _ in device.direct_peers()),
    )
    return PeerSync(device.sync_node, directory, interval=interval, listen=False, should_sync=gate)


def _direct_sync(device: Device) -> DirectSync | None:
    if device.endpoint_id is None:
        logging.getLogger(__name__).info(
            "direct connections are off: install them with pip install 'ehor[p2p]'"
        )
        return None
    transport = IrohTransport(device.iroh_secret, IrohMode.INTERNET)
    return DirectSync(
        device.sync_node, transport, device.direct_peers, announce=device.announce_endpoint
    )


def _lan_sync(device: Device, interval: float) -> PeerSync:
    discovery = ZeroconfDiscovery(device.config.team, device.author)
    return PeerSync(device.sync_node, discovery, interval=interval)


def _log_to_stderr() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


async def _run_until_signal(run: Callable[[asyncio.Event], Awaitable[None]]) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await run(stop)
    except asyncio.CancelledError:
        stop.set()


def _mcp(args: argparse.Namespace) -> int:
    build_server(RpcClient(default_endpoint(_home(args)))).run("stdio")
    return 0


def _tool(args: argparse.Namespace) -> int:
    return _call(args, args.tool, {})
