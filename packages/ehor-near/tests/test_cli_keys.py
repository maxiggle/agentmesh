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
from ehor.identity.es256 import verify
from ehor.identity.software import SoftwareSigner
from ehor.service.device import Device
from ehor_near import cli as cli_module
from ehor_near.cli import RELEASE_KEY_LABEL
from ehor_near.client import DEFAULT_GAS, NearClient
from ehor_near.config import NearConfig
from ehor_near.payload import KeyAction, encryption_key_payload

CONTRACT = "ehor-escrow.testnet"
ME = "geff.testnet"
PASSPHRASE = "test passphrase"  # noqa: S105 - test fixture value
OTHER_KEY = b"\x44" * 32


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="ehor") as directory:
        yield Path(directory)


@pytest.fixture(autouse=True)
def passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EHOR_PASSPHRASE", PASSPHRASE)
    monkeypatch.setattr(cli_module, "KEY_CONFIRM_DELAY", 0.0)


@dataclass
class TouchSigner:
    inner: SoftwareSigner
    reasons: list[str] = field(default_factory=list)

    def create_key(self, label: str) -> KeyHandle:
        return self.inner.create_key(label)

    def public_key(self, handle: KeyHandle) -> bytes:
        return self.inner.public_key(KeyHandle("software", handle.key_id))

    def sign_with_presence(self, handle: KeyHandle, data: bytes, reason: str) -> bytes:
        self.reasons.append(reason)
        return self.inner.sign(KeyHandle("software", handle.key_id), data)


@dataclass
class Chain:
    """Enough of ehor-escrow to check `ehor near keys`; it verifies key signatures as the
    contract does."""

    release_key: bytes = b""
    nonce: int = 0
    keys: list[bytes] = field(default_factory=list)

    async def view(self, contract: str, method: str, args: Mapping[str, object]) -> object:
        assert contract == CONTRACT
        assert args == {"account_id": ME}
        if method == "get_account":
            return {"device_key": self.release_key.hex()}
        assert method == "get_encryption_keys"
        return {"nonce": self.nonce, "keys": [key.hex() for key in self.keys]}

    def apply(self, method: str, args: Mapping[str, object]) -> None:
        action = KeyAction(method.removesuffix("_encryption_key"))
        key = bytes.fromhex(str(args["x25519_public_key"]))
        payload = encryption_key_payload(
            "testnet", CONTRACT, ME, nonce=self.nonce, action=action, key=key
        )
        signature = bytes.fromhex(str(args["signature"]))
        assert verify(self.release_key, signature, payload.encode()), "ERR_BAD_SIGNATURE"
        self.nonce += 1
        if action is KeyAction.ADD:
            self.keys.append(key)
        else:
            self.keys.remove(key)

    async def call(
        self,
        contract: str,
        method: str,
        args: Mapping[str, object],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> object:
        raise AssertionError("key changes are signed in the wallet, never on the Mac")

    async def access_key(self, account: str, public_key: str) -> object | None:
        raise AssertionError("key changes do not touch access keys")

    async def add_function_call_key(
        self, public_key: str, contract: str, methods: Sequence[str], allowance_yocto: int
    ) -> None:
        raise AssertionError("key changes do not add access keys")


@dataclass
class Wallet:
    chain: Chain
    account: str = ME
    configs: list[dict[str, object]] = field(default_factory=list)
    requests: list[dict[str, object]] = field(default_factory=list)
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
        response = connection.getresponse()
        data: dict[str, object] = (
            json.loads(response.read() or b"{}") if response.status == 200 else {}
        )
        return data

    def _drive(self, url: str) -> None:
        parsed = urlparse(url)
        connection = http.client.HTTPConnection(parsed.netloc, timeout=10)
        connection.request("GET", f"/?{parsed.query}")
        page = connection.getresponse().read().decode()
        start = page.index('id="config">') + len('id="config">')
        self.configs.append(json.loads(page[start : page.index("</script>", start)]))
        plan = self._post(url, "/connected", {"accountId": self.account, "walletId": "meteor"})
        request = plan.get("request")
        if isinstance(request, dict):
            self.requests.append(request)
            args = request["args"]
            assert isinstance(args, dict)
            self.chain.apply(str(request["method"]), args)
            self._post(url, "/done", {"transactionHash": "tx1"})


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "gef"
    Device.create_team(path, "gef", PASSPHRASE.encode(), team_name="Acme").close()
    return path


@pytest.fixture
def signer(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> TouchSigner:
    touch = TouchSigner(SoftwareSigner(tmp_path / "release", b"pw"))
    touch.create_key(RELEASE_KEY_LABEL)
    release = touch.public_key(KeyHandle("x", RELEASE_KEY_LABEL))
    NearConfig(ME, CONTRACT, release_key=release.hex()).save(home)
    monkeypatch.setattr(cli_module, "_release_signer", lambda home: touch)
    return touch


@pytest.fixture
def chain(signer: TouchSigner, monkeypatch: pytest.MonkeyPatch) -> Chain:
    fake = Chain(release_key=signer.public_key(KeyHandle("x", RELEASE_KEY_LABEL)))

    @asynccontextmanager
    async def connect(config: NearConfig) -> AsyncIterator[NearClient]:
        yield fake

    monkeypatch.setattr(cli_module, "_connect_view", connect)
    return fake


@pytest.fixture
def wallet(chain: Chain, monkeypatch: pytest.MonkeyPatch) -> Iterator[Wallet]:
    fake = Wallet(chain)
    monkeypatch.setattr(cli_module, "_open_browser", fake.open)
    yield fake
    if fake.thread is not None:
        fake.thread.join(10)


def _keys(home: Path, *args: str) -> int:
    return main(["--home", str(home), "near", "keys", *args])


def _device_key(home: Path) -> bytes:
    with Device.open(home, PASSPHRASE.encode()) as device:
        return device.encryption_key


def test_add_approves_this_devices_key_with_touch_id_and_the_wallet(
    home: Path,
    signer: TouchSigner,
    chain: Chain,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mine = _device_key(home)
    assert _keys(home, "add") == 0
    assert chain.keys == [mine]
    assert chain.nonce == 1
    assert signer.reasons == [f"approve ehor encryption key {mine.hex()[:16]} for {ME}"]
    assert wallet.configs[0]["publicKey"] is None
    request = wallet.requests[0]
    assert (request["method"], request["deposit"]) == ("add_encryption_key", "1")
    assert capsys.readouterr().out.rstrip().endswith(f"{ME} approved encryption key {mine.hex()}")


def test_add_takes_another_devices_key(
    home: Path, signer: TouchSigner, chain: Chain, wallet: Wallet
) -> None:
    assert _keys(home, "add", OTHER_KEY.hex()) == 0
    assert chain.keys == [OTHER_KEY]


def test_an_approved_key_needs_no_approval_again(
    home: Path,
    signer: TouchSigner,
    chain: Chain,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    chain.keys = [OTHER_KEY]
    assert _keys(home, "add", OTHER_KEY.hex()) == 0
    assert signer.reasons == []
    assert wallet.thread is None
    assert "nothing to do" in capsys.readouterr().out


def test_remove_withdraws_an_approved_key(
    home: Path, signer: TouchSigner, chain: Chain, wallet: Wallet
) -> None:
    chain.keys, chain.nonce = [OTHER_KEY], 4
    assert _keys(home, "remove", OTHER_KEY.hex()) == 0
    assert chain.keys == []
    assert chain.nonce == 5
    assert wallet.requests[0]["method"] == "remove_encryption_key"
    assert signer.reasons[0].startswith("remove ehor encryption key")


def test_removing_a_key_that_is_not_approved_is_refused(
    home: Path,
    signer: TouchSigner,
    chain: Chain,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _keys(home, "remove", OTHER_KEY.hex()) == 1
    assert "has not approved" in capsys.readouterr().err
    assert signer.reasons == []


def test_another_release_key_on_chain_stops_before_touch_id(
    home: Path,
    signer: TouchSigner,
    chain: Chain,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    chain.release_key = b"\x02" + b"\x11" * 32
    assert _keys(home, "add") == 1
    assert "different release key" in capsys.readouterr().err
    assert signer.reasons == []
    assert wallet.thread is None


def test_a_wallet_signed_in_as_another_account_is_refused(
    home: Path,
    signer: TouchSigner,
    chain: Chain,
    wallet: Wallet,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wallet.account = "eve.testnet"
    assert _keys(home, "add") == 1
    assert "the wallet connected eve.testnet, not geff.testnet" in capsys.readouterr().err
    assert chain.keys == []


@pytest.mark.parametrize("key", ["zz" * 32, "ab" * 31])
def test_a_malformed_key_is_refused(
    home: Path, signer: TouchSigner, chain: Chain, key: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _keys(home, "add", key) == 1
    assert "encryption key" in capsys.readouterr().err
    assert signer.reasons == []


def test_list_shows_whether_this_device_is_approved(
    home: Path, signer: TouchSigner, chain: Chain, capsys: pytest.CaptureFixture[str]
) -> None:
    mine = _device_key(home)
    assert _keys(home, "list") == 0
    assert "has approved no encryption keys" in capsys.readouterr().out
    chain.keys = [OTHER_KEY]
    assert _keys(home, "list") == 0
    assert "This device's key is not approved" in capsys.readouterr().out
    chain.keys = [OTHER_KEY, mine]
    assert _keys(home, "list") == 0
    out = capsys.readouterr().out
    assert f"  {mine.hex()}  (this device)" in out
    assert "not approved" not in out


def test_keys_need_near_setup_first(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _keys(home, "list") == 1
    assert "run `ehor near setup` first" in capsys.readouterr().err
