from pathlib import Path

import pytest

from agentmesh.identity import KeyHandle
from agentmesh.identity.software import SoftwareSigner
from agentmesh.log.verify import EntryVerifier
from agentmesh.service.api import ApiError, ToolApi
from agentmesh.service.messenger import Identity, Messenger
from agentmesh.store.local_log import LocalLog
from agentmesh.store.sqlite import SqliteStore


@pytest.fixture
def api(tmp_db: Path, signer: SoftwareSigner, handle: KeyHandle, team: bytes) -> ToolApi:
    store = SqliteStore(tmp_db)
    local_log = LocalLog(store, signer, handle, team, EntryVerifier(team))
    return ToolApi(Messenger(store, local_log, Identity("gef")))


def test_exposes_the_seven_tools(api: ToolApi) -> None:
    assert api.methods == {"digest", "inbox", "read", "post", "reply", "set_status", "team"}


def test_post_read_and_status_round_trip(api: ToolApi) -> None:
    posted = api.call("post", {"to": ["tobi"], "type": "feature_request", "summary": "Add retries"})
    assert posted.startswith("posted ")
    assert posted.endswith(" in dm:gef+tobi")
    message_id = posted.split()[1]
    assert '"Add retries"' in api.call("read", {"id": message_id})
    assert api.call("set_status", {"id": message_id.upper(), "status": "claimed"}) == (
        f"{message_id} is now claimed"
    )
    assert "status claimed by me" in api.call("read", {"id": message_id})
    assert api.call("reply", {"id": message_id, "summary": "on it"}).startswith("posted ")


def test_digest_inbox_and_team_take_no_required_params(api: ToolApi) -> None:
    assert api.call("digest", {}) == "nothing new"
    assert api.call("inbox", {"limit": 5}) == "no new messages · cursor 0"
    assert api.call("team", {}).startswith("me gef")


@pytest.mark.parametrize(
    ("method", "params", "error"),
    [
        ("delete", {}, "unknown method"),
        ("digest", {"verbose": True}, "unknown params: verbose"),
        ("post", {"to": "tobi", "type": "fyi", "summary": "hi"}, "to must be a list"),
        ("post", {"to": ["tobi"], "type": "fyi"}, "summary must be a string"),
        ("post", {"to": ["tobi"], "type": "fyi", "summary": "x" * 141}, "max 140"),
        ("inbox", {"limit": "5"}, "limit must be an integer"),
        ("inbox", {"since": True}, "since must be an integer"),
        ("read", {"id": "zz"}, "at least 6"),
    ],
)
def test_rejects_bad_calls(
    api: ToolApi, method: str, params: dict[str, object], error: str
) -> None:
    with pytest.raises(ApiError, match=error):
        api.call(method, params)
