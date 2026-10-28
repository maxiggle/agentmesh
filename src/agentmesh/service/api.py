from collections.abc import Callable, Mapping

from agentmesh.service import render
from agentmesh.service.messenger import Messenger, MessengerError


class ApiError(Exception):
    pass


class Params:
    """Typed access to JSON request params that refuses unknown or mistyped fields."""

    def __init__(self, raw: Mapping[str, object]) -> None:
        self._raw = dict(raw)
        self._used: set[str] = set()

    def text(self, name: str) -> str:
        value = self._take(name)
        if not isinstance(value, str):
            raise ApiError(f"{name} must be a string")
        return value

    def optional_text(self, name: str) -> str | None:
        return None if self._take(name) is None else self.text(name)

    def texts(self, name: str, *, required: bool = True) -> list[str]:
        value = self._take(name)
        if value is None and not required:
            return []
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ApiError(f"{name} must be a list of strings")
        return value

    def optional_int(self, name: str) -> int | None:
        value = self._take(name)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ApiError(f"{name} must be an integer")
        return value

    def finish(self) -> None:
        unknown = sorted(self._raw.keys() - self._used)
        if unknown:
            raise ApiError(f"unknown params: {', '.join(unknown)}")

    def _take(self, name: str) -> object:
        self._used.add(name)
        return self._raw.get(name)


class ToolApi:
    """Maps tool calls to messenger operations and renders their results as text."""

    def __init__(self, messenger: Messenger) -> None:
        self._messenger = messenger
        self._handlers: dict[str, Callable[[Params], str]] = {
            "digest": self._digest,
            "inbox": self._inbox,
            "read": self._read,
            "post": self._post,
            "reply": self._reply,
            "set_status": self._set_status,
            "team": self._team,
        }

    @property
    def methods(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def call(self, method: str, params: Mapping[str, object]) -> str:
        handler = self._handlers.get(method)
        if handler is None:
            raise ApiError(f"unknown method {method!r}")
        try:
            return handler(Params(params))
        except MessengerError as exc:
            raise ApiError(str(exc)) from exc

    def _digest(self, params: Params) -> str:
        params.finish()
        return render.digest(self._messenger.digest())

    def _inbox(self, params: Params) -> str:
        since = params.optional_int("since")
        channel = params.optional_text("channel")
        limit = params.optional_int("limit")
        params.finish()
        page = self._messenger.inbox(since=since, channel=channel, limit=limit)
        return render.inbox(page, self._messenger.author)

    def _read(self, params: Params) -> str:
        entry_id = params.text("id")
        params.finish()
        return render.detail(self._messenger.read(entry_id), self._messenger.author)

    def _post(self, params: Params) -> str:
        to = params.texts("to")
        message_type = params.text("type")
        summary = params.text("summary")
        links = params.texts("links", required=False)
        acceptance = params.optional_text("acceptance")
        detail = params.optional_text("detail")
        params.finish()
        entry = self._messenger.post(
            to, message_type, summary, links=links, acceptance=acceptance, detail=detail
        )
        return render.posted(entry)

    def _reply(self, params: Params) -> str:
        entry_id = params.text("id")
        summary = params.text("summary")
        links = params.texts("links", required=False)
        params.finish()
        return render.posted(self._messenger.reply(entry_id, summary, links=links))

    def _set_status(self, params: Params) -> str:
        entry_id = params.text("id")
        status = params.text("status")
        note = params.optional_text("note")
        params.finish()
        self._messenger.set_status(entry_id, status, note)
        return f"{entry_id.strip().lower()} is now {status}"

    def _team(self, params: Params) -> str:
        params.finish()
        return render.team(self._messenger.me, self._messenger.author, self._messenger.team())
