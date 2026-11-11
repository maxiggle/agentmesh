import asyncio
import hmac
import json
import secrets
import threading
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from types import TracebackType
from typing import Self
from urllib.parse import parse_qs, urlparse

from ehor_near.client import DEFAULT_GAS, is_account_id
from ehor_near.payload import format_near

NEAR_CONNECT_VERSION = "0.12.0"
REQUIRED_FEATURES = {
    "testnet": True,
    "signInWithFunctionCallKey": True,
    "signAndSendTransaction": True,
}
_HOST = "127.0.0.1"
_MAX_BODY = 4096
_MAX_ERROR = 300
_PLAN_TIMEOUT = 60.0


class WalletPageError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class WalletCall:
    """One contract call for the wallet to sign with its own full-access key."""

    receiver_id: str
    method: str
    args: Mapping[str, object]
    deposit_yocto: int
    summary: str
    gas: int = DEFAULT_GAS

    def to_json(self) -> dict[str, object]:
        return {
            "receiverId": self.receiver_id,
            "method": self.method,
            "args": dict(self.args),
            "deposit": str(self.deposit_yocto),
            "gas": str(self.gas),
        }


@dataclass(frozen=True, slots=True)
class WalletLink:
    """What the page reported. Only a hint: callers confirm all of it by chain view."""

    account_id: str
    wallet_id: str
    transaction_hash: str | None


Planner = Callable[[str], Coroutine[object, object, WalletCall | None]]


@dataclass
class _State:
    account_id: str | None = None
    wallet_id: str = ""
    transaction_hash: str | None = None
    error: str | None = None
    finished: threading.Event = field(default_factory=threading.Event)


def _clean(text: str) -> str:
    return "".join(char if char.isprintable() else " " for char in text)[:_MAX_ERROR]


class WalletPage:
    """A one-time page on 127.0.0.1 where the person approves ehor's requests in their wallet.

    Every request must carry the page's random token and name this server as its Host, so other
    local pages and DNS-rebinding sites cannot read the page or report to it.
    """

    def __init__(
        self,
        *,
        network: str,
        contract: str,
        public_key: str,
        methods: Sequence[str],
        allowance_yocto: int,
        planner: Planner,
        plan_timeout: float = _PLAN_TIMEOUT,
    ) -> None:
        self._token = secrets.token_urlsafe(24)
        self._planner = planner
        self._plan_timeout = plan_timeout
        self._state = _State()
        self._loop: asyncio.AbstractEventLoop | None = None
        config = {
            "network": network,
            "contract": contract,
            "publicKey": public_key,
            "methods": list(methods),
            "allowance": str(allowance_yocto),
            "allowanceNear": format_near(allowance_yocto),
            "features": REQUIRED_FEATURES,
        }
        template = files("ehor_near").joinpath("web", "wallet.html").read_text(encoding="utf-8")
        embedded = json.dumps(config).replace("</", "<\\/")
        self._page = template.replace("__CONFIG__", embedded).encode("utf-8")
        self._script = files("ehor_near").joinpath("web", "near-connect.js").read_bytes()
        self._server = ThreadingHTTPServer((_HOST, 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self._authority}/?t={self._token}"

    @property
    def _authority(self) -> str:
        return f"{_HOST}:{self._server.server_address[1]}"

    def __enter__(self) -> Self:
        self._loop = asyncio.get_running_loop()
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()

    async def wait(self, timeout: float) -> WalletLink:
        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, self._state.finished.wait, timeout):
            raise WalletPageError(f"no answer from the wallet page within {timeout:g} seconds")
        state = self._state
        if state.error is not None:
            raise WalletPageError(f"the wallet page stopped: {state.error}")
        if state.account_id is None:
            raise WalletPageError("the wallet page finished without connecting an account")
        return WalletLink(state.account_id, state.wallet_id, state.transaction_hash)

    def _plan(self, account_id: str) -> WalletCall | None:
        if self._loop is None:
            raise WalletPageError("the wallet page is not running")
        future = asyncio.run_coroutine_threadsafe(self._planner(account_id), self._loop)
        call: WalletCall | None = future.result(self._plan_timeout)
        return call

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        page = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self._checked_path()
                if path == "/":
                    self._send(200, page._page, "text/html; charset=utf-8")
                elif path == "/near-connect.js":
                    self._send(200, page._script, "text/javascript; charset=utf-8")
                elif path is not None:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self) -> None:
                path = self._checked_path()
                if path is None:
                    return
                body = self._json()
                if body is None:
                    return
                if path == "/connected":
                    self._connected(body)
                elif path == "/done":
                    self._done(body)
                else:
                    self._send(404, b"not found", "text/plain")

            def _connected(self, body: dict[str, object]) -> None:
                state = page._state
                account = body.get("accountId")
                if state.account_id is not None or state.finished.is_set():
                    self._send(409, b"already connected", "text/plain")
                    return
                if not isinstance(account, str) or not is_account_id(account):
                    self._send(400, b"not a NEAR account id", "text/plain")
                    return
                wallet = body.get("walletId")
                state.account_id = account
                state.wallet_id = _clean(wallet) if isinstance(wallet, str) else ""
                try:
                    call = page._plan(account)
                except Exception as exc:
                    state.error = _clean(f"ehor could not prepare the request: {exc}")
                    state.finished.set()
                    self._send(500, state.error.encode(), "text/plain")
                    return
                answer = {
                    "request": None if call is None else call.to_json(),
                    "summary": "Nothing more to approve." if call is None else call.summary,
                }
                self._send(200, json.dumps(answer).encode(), "application/json")

            def _done(self, body: dict[str, object]) -> None:
                state = page._state
                if state.finished.is_set():
                    self._send(409, b"already finished", "text/plain")
                    return
                error = body.get("error")
                if error is not None:
                    state.error = _clean(str(error))
                tx = body.get("transactionHash")
                if isinstance(tx, str):
                    state.transaction_hash = _clean(tx)
                state.finished.set()
                self._send(200, b"{}", "application/json")

            def _checked_path(self) -> str | None:
                url = urlparse(self.path)
                token = parse_qs(url.query).get("t", [""])[0]
                if self.headers.get("Host") != page._authority or not hmac.compare_digest(
                    token, page._token
                ):
                    self._send(403, b"forbidden", "text/plain")
                    return None
                return url.path

            def _json(self) -> dict[str, object] | None:
                length = self.headers.get("Content-Length", "")
                if not length.isdigit() or int(length) > _MAX_BODY:
                    self._send(413, b"request too large", "text/plain")
                    return None
                try:
                    data = json.loads(self.rfile.read(int(length)))
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    self._send(400, b"expected a JSON object", "text/plain")
                    return None
                return data

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server API name
                pass

        return Handler
