import http.client
import json
import tempfile
import threading
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import pytest

from ehor.cli import main
from ehor.identity.base import KeyHandle
from ehor.identity.software import SoftwareSigner
from ehor.service.device import Device
from ehor_near import cli as cli_module
from ehor_near.cli import RELEASE_KEY_LABEL
from ehor_near.client import DEFAULT_GAS, NearClient
from ehor_near.config import NearConfig
from ehor_near.limited_key import LIMITED_KEY_FILE, LimitedKeyFile

CONTRACT = "ehor-escrow.testnet"
ME = "geff.testnet"
PASSPHRASE = "test passphrase"  # noqa: S105 - test fixture value


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="ehor") as directory:
        yield Path(directory)


@pytest.fixture(autouse=True)
def passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EHOR_PASSPHRASE", PASSPHRASE)


@dataclass
class TouchSigner:
    inner: SoftwareSigner
    created: list[str] = field(default_factory=list)

    def create_key(self, label: str) -> KeyHandle:
        self.created.append(label)
        self.inner.create_key(label)
        return KeyHandle("secure-enclave-presence", label)

    def public_key(self, handle: KeyHandle) -> bytes:
        return self.inner.public_key(KeyHandle("software", handle.key_id))

    def sign_with_presence(self, handle: KeyHandle, data: bytes, reason: str) -> bytes:
        raise AssertionError("setup never signs a release")


@dataclass
class Chain:
    accounts: dict[str, dict[str, object]] = field(default_factory=dict)
    keys: dict[tuple[str, str], object] = field(default_factory=dict)
    secrets: list[str] = field(default_factory=list)

    async def view(self, contract: str, method: str, args: Mapping[str, object]) -> object:
        assert (contract, method) == (CONTRACT, "get_account")
        return self.accounts.get(str(args["account_id"]))

    async def call(
        self,
        contract: str,
        method: str,
        args: Mapping[str, object],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> object:
        raise AssertionError("setup never signs on the Mac")

    async def access_key(self, account: str, public_key: str) -> object | None:
        return self.keys.get((account, public_key))

    async def add_function_call_key(
        self, public_key: str, contract: str, methods: Sequence[str], allowance_yocto: int
    ) -> None:
        raise AssertionError("setup never signs on the Mac")


@dataclass
class Wallet:
    """Plays the person's wallet: reads the page, adds the key, signs what it is asked."""

    chain: Chain
    account: str = ME
    permission: object = None
    requests: list[object] = field(default_factory=list)
    error: str | None = None
    thread: threading.Thread | None = None

    def open(self, url: str) -> None:
        self.thread = threading.Thread(target=self._drive, args=(url,))
        self.thread.start()

    def _post(self, url: str, path: str, body: object) -> dict[str, object]:
        parsed = urlparse(url)
        connection = http.client.HTTPConnection(parsed.netloc, timeout=10)
        connection.request(
            "POST", f"{path}?{parsed.query}", json.dumps(body), {"Content-Type": "application/json"}
        )
        data: dict[str, object] = json.loads(connection.getresponse().read() or b"{}")
        return data

    def _drive(self, url: str) -> None:
        parsed = urlparse(url)
        connection = http.client.HTTPConnection(parsed.netloc, timeout=10)
        connection.request("GET", f"/?{parsed.query}")
        page = connection.getresponse().read().decode()
        start = page.index('id="config">') + len('id="config">')
        config = json.loads(page[start : page.index("</script>", start)])
        if self.error:
            self._post(url, "/done", {"error": self.error})
            return
        permission = self.permission or {
            "FunctionCall": {
                "receiver_id": config["contract"],
                "method_names": [],
                "allowance": config["allowance"],
            }
        }
        self.chain.keys[(self.account, config["publicKey"])] = permission
        plan = self._post(url, "/connected", {"accountId": self.account, "walletId": "meteor"})
        request = plan["request"]
        self.requests.append(request)
        if isinstance(request, dict):
            args = request["args"]
            assert isinstance(args, dict)
            self.chain.accounts[self.account] = {"device_key": args["p256_public_key"]}
        self._post(url, "/done", {"transactionHash": "tx1" if request else None})


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "gef"
    Device.create_team(path, "gef", PASSPHRASE.encode(), team_name="Acme").close()
    return path


@pytest.fixture
def signer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TouchSigner:
    touch = TouchSigner(SoftwareSigner(tmp_path / "release", b"pw"))
    monkeypatch.setattr(cli_module, "_release_signer", lambda home: touch)
    return touch


@pytest.fixture
def chain(monkeypatch: pytest.MonkeyPatch) -> Chain:
    fake = Chain()

    @asynccontextmanager
    async def connect(config: NearConfig, private_key: str) -> AsyncIterator[NearClient]:
        assert private_key.startswith("ed25519:")
        fake.secrets.append(private_key)
        yield fake

    monkeypatch.setattr(cli_module, "_connect_near", connect)
    return fake


@pytest.fixture
def wallet(chain: Chain, monkeypatch: pytest.MonkeyPatch) -> Iterator[Wallet]:
    fake = Wallet(chain)
    monkeypatch.setattr(cli_module, "_open_browser", fake.open)
    yield fake
    if fake.thread is not None:
        fake.thread.join(10)


def _setup(home: Path, *args: str) -> int:
    return main(["--home", str(home), "near", "setup", *args])


def _release_hex(signer: TouchSigner) -> str:
    return signer.public_key(KeyHandle("x", RELEASE_KEY_LABEL)).hex()


def test_setup_registers_a_new_account(
    home: Path,
    signer: TouchSigner,
    chain: Chain,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _setup(home, "--deposit", "2", "--max-per-bounty", "0.25") == 0
    out = capsys.readouterr().out
    assert "created this Mac's Touch ID release key" in out
    assert "deposit of 2 NEAR and a limit of 0.25 NEAR" in out
    assert out.rstrip().endswith("sends them with the limited key.")
    assert signer.created == [RELEASE_KEY_LABEL]
    assert wallet.requests == [
        {
            "receiverId": CONTRACT,
            "method": "register",
            "args": {"p256_public_key": _release_hex(signer), "max_per_bounty": str(25 * 10**22)},
            "deposit": str(2 * 10**24),
            "gas": str(DEFAULT_GAS),
        }
    ]
    assert NearConfig.load(home) == NearConfig(ME, CONTRACT)
    limited = LimitedKeyFile(home / "keys" / LIMITED_KEY_FILE, PASSPHRASE.encode()).load()
    assert (ME, limited.public_key) in chain.keys
    assert set(chain.secrets) == {limited.near_secret()}
    assert limited.near_secret() not in out


def test_setup_replaces_another_release_key(
    home: Path, signer: TouchSigner, chain: Chain, wallet: Wallet
) -> None:
    chain.accounts[ME] = {"device_key": "02" + "11" * 32}
    assert _setup(home) == 0
    request = wallet.requests[0]
    assert isinstance(request, dict)
    assert (request["method"], request["deposit"]) == ("set_device_key", "1")
    assert chain.accounts[ME]["device_key"] == _release_hex(signer)


def _set_up_once(home: Path, wallet: Wallet) -> None:
    assert _setup(home) == 0
    assert wallet.thread is not None
    wallet.thread.join(10)
    wallet.thread = None


def test_setup_run_again_skips_the_wallet_when_both_keys_are_on_chain(
    home: Path,
    signer: TouchSigner,
    chain: Chain,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set_up_once(home, wallet)
    first = LimitedKeyFile(home / "keys" / LIMITED_KEY_FILE, PASSPHRASE.encode()).load()
    assert _setup(home) == 0
    again = LimitedKeyFile(home / "keys" / LIMITED_KEY_FILE, PASSPHRASE.encode()).load()
    assert again.public_key == first.public_key
    assert signer.created == [RELEASE_KEY_LABEL]
    assert wallet.thread is None
    assert len(wallet.requests) == 1
    out = capsys.readouterr().out
    assert out.count("Your browser will open") == 1
    assert out.rstrip().endswith("so there is nothing to approve.")


def test_setup_run_again_opens_the_wallet_when_the_release_key_changed(
    home: Path, signer: TouchSigner, chain: Chain, wallet: Wallet
) -> None:
    _set_up_once(home, wallet)
    chain.accounts[ME] = {"device_key": "02" + "11" * 32}
    assert _setup(home) == 0
    request = wallet.requests[-1]
    assert isinstance(request, dict)
    assert request["method"] == "set_device_key"


def test_setup_for_another_contract_opens_the_wallet(
    home: Path, signer: TouchSigner, chain: Chain, wallet: Wallet
) -> None:
    NearConfig(ME, "other-escrow.testnet").save(home)
    assert _setup(home) == 0
    assert wallet.thread is not None
    assert len(wallet.requests) == 1


def test_a_full_access_key_from_the_wallet_is_refused(
    home: Path,
    signer: TouchSigner,
    chain: Chain,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wallet.permission = "FullAccess"
    assert _setup(home) == 1
    err = capsys.readouterr().err
    assert "full access" in err
    assert "Remove the key ed25519:" in err
    assert NearConfig.load(home) is None


def test_a_wallet_error_stops_setup(
    home: Path,
    signer: TouchSigner,
    chain: Chain,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wallet.error = "User closed the window"
    assert _setup(home) == 1
    assert "User closed the window" in capsys.readouterr().err
    assert NearConfig.load(home) is None


def test_a_wrong_passphrase_stops_before_the_browser(
    home: Path,
    signer: TouchSigner,
    wallet: Wallet,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("EHOR_PASSPHRASE", "wrong")
    assert _setup(home) == 1
    assert "does not unlock this device" in capsys.readouterr().err
    assert wallet.thread is None
    assert not (home / "keys" / LIMITED_KEY_FILE).exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--deposit", "0"], "more than 0"),
        (["--max-per-bounty", "lots"], "not a NEAR amount"),
        (["--contract", "Not An Account"], "not a NEAR account id"),
    ],
)
def test_bad_arguments_stop_before_anything_is_created(
    home: Path,
    signer: TouchSigner,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    assert _setup(home, *args) == 1
    assert message in capsys.readouterr().err
    assert signer.created == []
    assert wallet.thread is None


def test_setup_is_mac_only(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert _setup(home) == 1
    assert "need a Mac" in capsys.readouterr().err
