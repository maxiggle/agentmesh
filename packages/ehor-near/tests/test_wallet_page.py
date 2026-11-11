import asyncio
import hashlib
import http.client
import json
from collections.abc import Callable
from importlib.resources import files
from urllib.parse import urlparse

import pytest

from ehor_near.wallet_page import (
    NEAR_CONNECT_VERSION,
    Planner,
    WalletCall,
    WalletLink,
    WalletPage,
    WalletPageError,
)

CONTRACT = "ehor-escrow.testnet"
PUBLIC_KEY = "ed25519:" + "4" * 44
NEAR_CONNECT_SHA256 = "bfd50427c2f1e98e85f42bdf08d3a923f5b17dce11590dd83bf47e6afc56a027"
REGISTER = WalletCall(
    CONTRACT, "register", {"p256_public_key": "02ab", "max_per_bounty": "5"}, 10**24, "Deposit 1"
)

Response = tuple[int, bytes]


def _request(
    url: str, method: str, path: str, body: object = None, *, host: str | None = None
) -> Response:
    parsed = urlparse(url)
    token = parsed.query
    connection = http.client.HTTPConnection(parsed.netloc, timeout=5)
    headers = {"Host": host or parsed.netloc}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    connection.request(method, f"{path}?{token}", body=data, headers=headers)
    response = connection.getresponse()
    return response.status, response.read()


def _page(planner: Planner | None = None) -> WalletPage:
    async def plan(account: str) -> WalletCall | None:
        return REGISTER

    return WalletPage(
        network="testnet",
        contract=CONTRACT,
        public_key=PUBLIC_KEY,
        methods=["claim"],
        allowance_yocto=25 * 10**22,
        planner=planner or plan,
    )


def _run(
    steps: Callable[[str], None],
    planner: Planner | None = None,
    timeout: float = 5,
) -> WalletLink:
    async def main() -> WalletLink:
        with _page(planner) as page:
            loop = asyncio.get_running_loop()
            waiting = asyncio.ensure_future(page.wait(timeout))
            await loop.run_in_executor(None, steps, page.url)
            return await waiting

    return asyncio.run(main())


def test_full_flow_returns_what_the_page_reported() -> None:
    answers: list[Response] = []

    def steps(url: str) -> None:
        answers.append(_request(url, "POST", "/connected", {"accountId": "geff.testnet"}))
        answers.append(_request(url, "POST", "/done", {"transactionHash": "abc"}))

    link = _run(steps)
    assert link == WalletLink("geff.testnet", "", "abc")
    status, body = answers[0]
    assert status == 200
    assert json.loads(body) == {
        "request": {
            "receiverId": CONTRACT,
            "method": "register",
            "args": {"p256_public_key": "02ab", "max_per_bounty": "5"},
            "deposit": str(10**24),
            "gas": str(100 * 10**12),
        },
        "summary": "Deposit 1",
    }


def test_nothing_to_sign_is_reported_as_such() -> None:
    async def nothing(account: str) -> WalletCall | None:
        return None

    answers: list[Response] = []

    def steps(url: str) -> None:
        answers.append(_request(url, "POST", "/connected", {"accountId": "geff.testnet"}))
        _request(url, "POST", "/done", {})

    assert _run(steps, nothing).transaction_hash is None
    assert json.loads(answers[0][1]) == {"request": None, "summary": "Nothing more to approve."}


def test_the_page_embeds_its_config_and_serves_the_pinned_bundle() -> None:
    answers: list[Response] = []

    def steps(url: str) -> None:
        answers.append(_request(url, "GET", "/"))
        answers.append(_request(url, "GET", "/near-connect.js"))
        _request(url, "POST", "/connected", {"accountId": "geff.testnet"})
        _request(url, "POST", "/done", {})

    _run(steps)
    (page_status, page), (script_status, script) = answers
    assert page_status == script_status == 200
    assert b"__CONFIG__" not in page
    assert PUBLIC_KEY.encode() in page
    assert b'"signInWithFunctionCallKey": true' in page
    assert hashlib.sha256(script).hexdigest() == NEAR_CONNECT_SHA256


def test_bundle_is_the_reviewed_version() -> None:
    script = files("ehor_near").joinpath("web", "near-connect.js").read_bytes()
    assert script.startswith(f"/*! @hot-labs/near-connect {NEAR_CONNECT_VERSION} (MIT)".encode())
    assert hashlib.sha256(script).hexdigest() == NEAR_CONNECT_SHA256


@pytest.mark.parametrize(
    ("change", "status"),
    [("token", 403), ("host", 403)],
)
def test_requests_without_the_token_or_from_another_host_are_refused(
    change: str, status: int
) -> None:
    answers: list[Response] = []

    def steps(url: str) -> None:
        bad = url.split("?", maxsplit=1)[0] + "?t=wrong" if change == "token" else url
        host = "evil.example:80" if change == "host" else None
        answers.append(_request(bad, "GET", "/", host=host))
        answers.append(
            _request(bad, "POST", "/connected", {"accountId": "mallory.testnet"}, host=host)
        )
        _request(url, "POST", "/connected", {"accountId": "geff.testnet"})
        _request(url, "POST", "/done", {})

    assert _run(steps).account_id == "geff.testnet"
    assert [code for code, _ in answers] == [status, status]


def test_a_bad_account_id_is_refused_and_a_second_connect_conflicts() -> None:
    answers: list[Response] = []

    def steps(url: str) -> None:
        answers.append(_request(url, "POST", "/connected", {"accountId": "Not Valid"}))
        answers.append(_request(url, "POST", "/connected", {"accountId": "geff.testnet"}))
        answers.append(_request(url, "POST", "/connected", {"accountId": "mallory.testnet"}))
        _request(url, "POST", "/done", {})
        answers.append(_request(url, "POST", "/done", {"transactionHash": "late"}))

    link = _run(steps)
    assert [code for code, _ in answers] == [400, 200, 409, 409]
    assert link.account_id == "geff.testnet"
    assert link.transaction_hash is None


@pytest.mark.parametrize("body", ["[]", "not json", "x" * 5000])
def test_malformed_bodies_are_refused(body: str) -> None:
    answers: list[int] = []

    def steps(url: str) -> None:
        parsed = urlparse(url)
        connection = http.client.HTTPConnection(parsed.netloc, timeout=5)
        connection.request("POST", f"/connected?{parsed.query}", body=body.encode())
        answers.append(connection.getresponse().status)
        _request(url, "POST", "/connected", {"accountId": "geff.testnet"})
        _request(url, "POST", "/done", {})

    _run(steps)
    assert answers[0] in {400, 413}


def test_a_wallet_error_reaches_the_caller_cleaned() -> None:
    def steps(url: str) -> None:
        _request(url, "POST", "/done", {"error": "User closed the window\n\x1b[31m" + "x" * 500})

    with pytest.raises(WalletPageError, match="User closed the window") as caught:
        _run(steps)
    assert "\x1b" not in str(caught.value)
    assert len(str(caught.value)) < 400


def test_a_planner_failure_stops_the_page() -> None:
    async def broken(account: str) -> WalletCall | None:
        raise RuntimeError("RPC is down")

    answers: list[Response] = []

    def steps(url: str) -> None:
        answers.append(_request(url, "POST", "/connected", {"accountId": "geff.testnet"}))

    with pytest.raises(WalletPageError, match="RPC is down"):
        _run(steps, broken)
    assert answers[0][0] == 500


def test_no_answer_times_out() -> None:
    with pytest.raises(WalletPageError, match="no answer"):
        _run(lambda url: None, timeout=0.2)


def test_finishing_without_an_account_is_an_error() -> None:
    def steps(url: str) -> None:
        _request(url, "POST", "/done", {})

    with pytest.raises(WalletPageError, match="without connecting"):
        _run(steps)
