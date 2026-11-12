from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from ehor.crypto.keyfile import KeyFileError
from ehor.log.account_announce import AccountAnnounce
from ehor.mcp.server import Relay
from ehor.service.api import Params
from ehor.service.device import key_dir
from ehor.service.messenger import MessengerError
from ehor.service.plugins import PluginHost, ToolHandler
from ehor_near.bounties import DEFAULT_HOURS, NEAR_SYSTEM, BountyDesk, BountyError
from ehor_near.client import NearError
from ehor_near.config import NearConfig, NearConfigError
from ehor_near.limited_key import LIMITED_KEY_FILE, LimitedKeyError, LimitedKeyFile
from ehor_near.pay import PayError
from ehor_near.payload import PayloadError
from ehor_near.pynear import PyNearClient

POST = "near.bounty_post"
CLAIM = "near.bounty_claim"
DONE = "near.bounty_done"
LIST = "near.bounties"
_RUN_SETUP = "your user can run `ehor near setup`"
_NOT_SET_UP = f"this device has no NEAR account; {_RUN_SETUP}"
_SET_UP_AGAIN = (
    "this device has not announced its NEAR account; your user can run `ehor near setup` "
    "again and restart `ehor serve`"
)
_ERRORS = (
    MessengerError,
    NearConfigError,
    NearError,
    PayError,
    PayloadError,
)

_SETUP_ERRORS = (KeyFileError, LimitedKeyError, NearConfigError)

Step = Callable[[Params], Awaitable[str]]


class NearTools:
    """Bounty tools for agents. None of them can sign or send a release; `ehor pay` does that."""

    def add_tools(self, server: MCPServer, relay: Relay) -> None:
        @server.tool(
            description=(
                "Put a NEAR bounty on your feature_request. near: amount, e.g. 0.1. "
                "Show the user amount, claimant and deadline; call only after they approve."
            )
        )
        async def bounty_post(
            id: str,  # noqa: A002 - tool parameter name shown to the agent
            near: str,
            hours: int = DEFAULT_HOURS,
            claimant: str | None = None,
        ) -> str:
            return await relay(POST, id=id, near=near, hours=hours, claimant=claimant)

        @server.tool(
            description=(
                "Claim the NEAR bounty on a teammate's request and mark it claimed. "
                "Call only after your user approves."
            )
        )
        async def bounty_claim(id: str, bounty: int) -> str:  # noqa: A002 - tool parameter
            return await relay(CLAIM, id=id, bounty=bounty)

        @server.tool(
            description=(
                "Mark the NEAR bounty you claimed done, and the request done. "
                "Call only after your user approves."
            )
        )
        async def bounty_done(
            id: str,  # noqa: A002 - tool parameter name shown to the agent
            bounty: int,
            note: str | None = None,
        ) -> str:
            return await relay(DONE, id=id, bounty=bounty, note=note)

        @server.tool(description="NEAR bounties you posted or claimed, read from the chain.")
        async def bounties() -> str:
            return await relay(LIST)

    def open(self, home: Path, passphrase: bytes, host: PluginHost) -> Mapping[str, ToolHandler]:
        try:
            desk = _desk(home, passphrase, host)
        except _SETUP_ERRORS as exc:
            desk = f"the NEAR setup on this device is broken: {exc}; {_RUN_SETUP}"

        async def post(params: Params) -> str:
            message_id = params.text("id")
            amount = params.text("near")
            hours = params.optional_int("hours") or DEFAULT_HOURS
            claimant = params.optional_text("claimant")
            params.finish()
            return await _ready(desk).post(message_id, amount, hours, claimant)

        async def claim(params: Params) -> str:
            message_id = params.text("id")
            bounty_id = _bounty_id(params)
            params.finish()
            return await _ready(desk).claim(message_id, bounty_id)

        async def done(params: Params) -> str:
            message_id = params.text("id")
            bounty_id = _bounty_id(params)
            note = params.optional_text("note")
            params.finish()
            return await _ready(desk).finish(message_id, bounty_id, note)

        async def listing(params: Params) -> str:
            params.finish()
            return await _ready(desk).overview()

        return {
            POST: _guarded(post),
            CLAIM: _guarded(claim),
            DONE: _guarded(done),
            LIST: _guarded(listing),
        }


tools = NearTools()


def _desk(home: Path, passphrase: bytes, host: PluginHost) -> BountyDesk | str:
    config = NearConfig.load(home)
    key_file = LimitedKeyFile(key_dir(home) / LIMITED_KEY_FILE, passphrase)
    if config is None or not key_file.exists():
        return _NOT_SET_UP
    if config.release_key is None:
        return _SET_UP_AGAIN
    host.announce_account(
        AccountAnnounce(NEAR_SYSTEM, config.account_id, bytes.fromhex(config.release_key))
    )
    secret = key_file.load().near_secret()
    return BountyDesk(
        home,
        config,
        lambda: PyNearClient.connect(config.account_id, secret, config.rpc_url),
        messages=host.messenger,
        accounts=host,
    )


def _ready(desk: BountyDesk | str) -> BountyDesk:
    if isinstance(desk, str):
        raise BountyError(desk)
    return desk


def _bounty_id(params: Params) -> int:
    bounty_id = params.optional_int("bounty")
    if bounty_id is None or bounty_id < 1:
        raise BountyError("bounty must be a bounty number, as given in the request's replies")
    return bounty_id


def _guarded(step: Step) -> ToolHandler:
    async def handler(raw: Mapping[str, object]) -> str:
        try:
            return await step(Params(raw))
        except _ERRORS as exc:
            raise BountyError(str(exc)) from exc

    return handler
