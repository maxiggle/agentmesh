from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from agentmesh.log.entry import Entry, Kind
from agentmesh.log.names import CONTROL_CHANNEL
from agentmesh.log.roster import ALL_CHANNELS, Member, Roster, RosterDevice, RosterError, Trust
from agentmesh.roster.book import RosterBook
from agentmesh.service.api import ApiError, Params
from agentmesh.store.local_log import LocalLog, NotAuthorizedError


class RosterAdminError(Exception):
    pass


class RosterAdmin:
    """Builds and signs roster changes. Only admin devices can publish them."""

    def __init__(self, book: RosterBook, local_log: LocalLog) -> None:
        self._book = book
        self._log = local_log

    def add(
        self,
        handle: str,
        key: bytes,
        *,
        roles: Sequence[str] = (),
        channels: Sequence[str] = (ALL_CHANNELS,),
        admin: bool = False,
        label: str = "",
    ) -> Entry:
        current, current_id = self._current()
        if current.member_for_key(key) is not None:
            raise RosterAdminError("that device key is already on the roster")
        try:
            device = RosterDevice(key, Trust.SOFTWARE, label or handle)
            existing = current.member(handle)
            if existing is None:
                member = Member(
                    handle, tuple(sorted(set(roles))), tuple(sorted(set(channels))), (device,)
                )
            else:
                devices = tuple(sorted((*existing.devices, device), key=lambda d: d.key))
                member = replace(existing, devices=devices)
            members = (*(m for m in current.members if m.handle != handle), member)
            admins = (*current.admins, handle) if admin and handle not in current.admins else None
            roster = current.successor(current_id, members=members, admins=admins)
        except RosterError as exc:
            raise RosterAdminError(str(exc)) from exc
        return self._publish(roster)

    def remove(self, handle: str) -> Entry:
        current, current_id = self._current()
        if current.member(handle) is None:
            raise RosterAdminError(f"{handle} is not on the roster")
        if current.admins == (handle,):
            raise RosterAdminError("cannot remove the last admin")
        members = tuple(m for m in current.members if m.handle != handle)
        admins = tuple(a for a in current.admins if a != handle)
        return self._publish(current.successor(current_id, members=members, admins=admins))

    def show(self) -> str:
        current = self._book.current
        if current is None:
            return "no roster yet: waiting to receive the team's genesis roster"
        lines = [f"{current.team_name or 'team'} · roster v{current.version}"]
        if self._book.forked:
            lines.append("warning: competing rosters; an admin must publish the next version")
        for member in current.members:
            tags = ["admin"] if member.handle in current.admins else []
            tags += list(member.roles)
            keys = ", ".join(f"{device.label} {device.key[1:4].hex()}" for device in member.devices)
            extra = f" ({', '.join(tags)})" if tags else ""
            lines.append(f"{member.handle}{extra} · channels {', '.join(member.channels)} · {keys}")
        return "\n".join(lines)

    def _current(self) -> tuple[Roster, bytes]:
        current, current_id = self._book.current, self._book.current_id
        if current is None or current_id is None:
            raise RosterAdminError("no roster yet")
        if self._book.forked:
            raise RosterAdminError("competing rosters exist; resolve them before changing")
        return current, current_id

    def _publish(self, roster: Roster) -> Entry:
        try:
            return self._log.append(CONTROL_CHANNEL, Kind.ROSTER, roster.encode())
        except NotAuthorizedError as exc:
            raise RosterAdminError(f"only admins can change the roster ({exc})") from exc


class AdminApi:
    """RPC methods for roster changes. Deliberately not exposed as MCP tools."""

    PREFIX = "roster."

    def __init__(self, admin: RosterAdmin) -> None:
        self._admin = admin
        self._handlers: dict[str, Callable[[Params], str]] = {
            "roster.show": self._show,
            "roster.add": self._add,
            "roster.remove": self._remove,
        }

    def call(self, method: str, params: Mapping[str, object]) -> str:
        handler = self._handlers.get(method)
        if handler is None:
            raise ApiError(f"unknown method {method!r}")
        try:
            return handler(Params(params))
        except RosterAdminError as exc:
            raise ApiError(str(exc)) from exc

    def _show(self, params: Params) -> str:
        params.finish()
        return self._admin.show()

    def _add(self, params: Params) -> str:
        handle = params.text("handle")
        key_hex = params.text("key")
        roles = params.texts("roles", required=False)
        channels = params.texts("channels", required=False) or [ALL_CHANNELS]
        admin = params.flag("admin")
        label = params.optional_text("label") or ""
        params.finish()
        try:
            key = bytes.fromhex(key_hex)
        except ValueError as exc:
            raise ApiError("key must be hex") from exc
        self._admin.add(handle, key, roles=roles, channels=channels, admin=admin, label=label)
        return f"added {handle}\n{self._admin.show()}"

    def _remove(self, params: Params) -> str:
        handle = params.text("handle")
        params.finish()
        self._admin.remove(handle)
        return f"removed {handle}\n{self._admin.show()}"
