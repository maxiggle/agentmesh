from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from agentmesh.identity.assertion import Assertion
from agentmesh.log.entry import Entry, EntryContent, EntryFormatError, Kind
from agentmesh.log.names import CONTROL_CHANNEL
from agentmesh.log.roster import (
    ALL_CHANNELS,
    Member,
    RelayInfo,
    Roster,
    RosterDevice,
    RosterError,
    Trust,
)
from agentmesh.log.verify import RejectedEntryError
from agentmesh.roster.book import RosterBook
from agentmesh.service.api import ApiError, Params
from agentmesh.store.base import StoreError
from agentmesh.store.local_log import LocalLog, NotAuthorizedError

SECURITY_KEY_PARAM = "security_key"


class RosterAdminError(Exception):
    pass


class RosterAdmin:
    """Builds roster changes and gets them signed. Only admin devices can publish them.

    Each `next_*` method returns the roster that would follow the current one. An admin
    without a security key `publish`es it with this device's key; one with a security key
    asks for a `draft`, signs it with the key's touch, and hands it back to `submit`.
    """

    def __init__(self, book: RosterBook, local_log: LocalLog) -> None:
        self._book = book
        self._log = local_log

    def next_with_device(
        self,
        handle: str,
        key: bytes,
        *,
        roles: Sequence[str] = (),
        channels: Sequence[str] = (ALL_CHANNELS,),
        admin: bool = False,
        label: str = "",
        hardware: bool = False,
    ) -> Roster:
        """Add a device. `hardware` is the admin's word for where the key lives; there is
        no attestation, so it records what the joining device reported."""
        trust = Trust.HARDWARE if hardware else Trust.SOFTWARE
        return self._with_device(
            handle, key, trust, label or handle, roles=roles, channels=channels, admin=admin
        )

    def next_with_security_key(self, key: bytes, label: str = "") -> Roster:
        """Add a security key to this device's own admin, who must use it from then on."""
        current, _ = self._current()
        member = current.member_for_key(self._log.author)
        if member is None or member.handle not in current.admins:
            raise RosterAdminError("only admins enroll security keys")
        return self._with_device(member.handle, key, Trust.SECURITY_KEY, label or "security-key")

    def next_without_member(self, handle: str) -> Roster:
        current, current_id = self._current()
        if current.member(handle) is None:
            raise RosterAdminError(f"{handle} is not on the roster")
        if current.admins == (handle,):
            raise RosterAdminError("cannot remove the last admin")
        members = tuple(m for m in current.members if m.handle != handle)
        admins = tuple(a for a in current.admins if a != handle)
        return current.successor(current_id, members=members, admins=admins)

    def next_with_relay(self, address: str, key: bytes) -> Roster:
        current, current_id = self._current()
        if current.relay_for_key(key) is not None:
            raise RosterAdminError("that relay is already on the roster")
        try:
            return current.successor(current_id, relays=(*current.relays, RelayInfo(key, address)))
        except RosterError as exc:
            raise RosterAdminError(str(exc)) from exc

    def next_without_relay(self, address: str) -> Roster:
        current, current_id = self._current()
        relays = tuple(relay for relay in current.relays if relay.address != address)
        if len(relays) == len(current.relays):
            raise RosterAdminError(f"no relay at {address}")
        return current.successor(current_id, relays=relays)

    def publish(self, roster: Roster) -> Entry:
        try:
            return self._log.append(CONTROL_CHANNEL, Kind.ROSTER, roster.encode())
        except NotAuthorizedError as exc:
            raise RosterAdminError(f"only admins can change the roster ({exc})") from exc

    def draft(self, roster: Roster, security_key: bytes) -> EntryContent:
        """The roster entry for `security_key` to sign, checked first so nobody touches
        a key for a change that would be refused."""
        current, _ = self._current()
        device = current.device(security_key)
        if device is None or device.trust is not Trust.SECURITY_KEY:
            raise RosterAdminError("that security key is not on the roster; enroll it first")
        if not current.is_admin_device(security_key):
            raise RosterAdminError("that security key does not belong to an admin")
        return self._log.draft(CONTROL_CHANNEL, Kind.ROSTER, roster.encode(), author=security_key)

    def submit(self, content: EntryContent, assertion: Assertion) -> Entry:
        if content.kind is not Kind.ROSTER:
            raise RosterAdminError("only roster changes are signed with a security key")
        try:
            return self._log.commit(content.with_assertion(assertion))
        except (RejectedEntryError, NotAuthorizedError) as exc:
            raise RosterAdminError(f"roster change refused: {exc}") from exc
        except StoreError as exc:
            raise RosterAdminError(
                "the roster changed while waiting for the touch; run the command again"
            ) from exc

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
            keys = ", ".join(
                f"{device.label} {device.key[1:4].hex()} {device.trust}"
                for device in member.devices
            )
            extra = f" ({', '.join(tags)})" if tags else ""
            lines.append(f"{member.handle}{extra} · channels {', '.join(member.channels)} · {keys}")
        lines.extend(f"relay {relay.address} · {relay.key[1:4].hex()}" for relay in current.relays)
        return "\n".join(lines)

    def _with_device(
        self,
        handle: str,
        key: bytes,
        trust: Trust,
        label: str,
        *,
        roles: Sequence[str] = (),
        channels: Sequence[str] = (ALL_CHANNELS,),
        admin: bool = False,
    ) -> Roster:
        current, current_id = self._current()
        if current.device(key) is not None or current.relay_for_key(key) is not None:
            raise RosterAdminError("that key is already on the roster")
        try:
            device = RosterDevice(key, trust, label)
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
            return current.successor(current_id, members=members, admins=admins)
        except RosterError as exc:
            raise RosterAdminError(str(exc)) from exc

    def _current(self) -> tuple[Roster, bytes]:
        current, current_id = self._book.current, self._book.current_id
        if current is None or current_id is None:
            raise RosterAdminError("no roster yet")
        if self._book.forked:
            raise RosterAdminError("competing rosters exist; resolve them before changing")
        return current, current_id


class AdminApi:
    """RPC methods for roster changes. Deliberately not exposed as MCP tools.

    Changes take an optional `security_key` (hex). With it, the reply is the hex of the
    unsigned roster entry for that key to sign, and `roster.submit` publishes it.
    """

    PREFIX = "roster."

    def __init__(self, admin: RosterAdmin) -> None:
        self._admin = admin
        self._handlers: dict[str, Callable[[Params], str]] = {
            "roster.show": self._show,
            "roster.add": self._add,
            "roster.remove": self._remove,
            "roster.relay_add": self._relay_add,
            "roster.relay_remove": self._relay_remove,
            "roster.enroll": self._enroll,
            "roster.submit": self._submit,
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
        key = _hex(params.text("key"), "key")
        roles = params.texts("roles", required=False)
        channels = params.texts("channels", required=False) or [ALL_CHANNELS]
        admin = params.flag("admin")
        label = params.optional_text("label") or ""
        hardware = params.flag("hardware")
        via = _security_key(params)
        roster = self._admin.next_with_device(
            handle, key, roles=roles, channels=channels, admin=admin, label=label, hardware=hardware
        )
        return self._sign(roster, via, f"added {handle}")

    def _remove(self, params: Params) -> str:
        handle = params.text("handle")
        via = _security_key(params)
        return self._sign(self._admin.next_without_member(handle), via, f"removed {handle}")

    def _relay_add(self, params: Params) -> str:
        address = params.text("address")
        key = _hex(params.text("key"), "key")
        via = _security_key(params)
        roster = self._admin.next_with_relay(address, key)
        return self._sign(roster, via, f"added relay {address}")

    def _relay_remove(self, params: Params) -> str:
        address = params.text("address")
        via = _security_key(params)
        roster = self._admin.next_without_relay(address)
        return self._sign(roster, via, f"removed relay {address}")

    def _enroll(self, params: Params) -> str:
        key = _hex(params.text("key"), "key")
        label = params.optional_text("label") or ""
        via = _security_key(params)
        roster = self._admin.next_with_security_key(key, label)
        return self._sign(roster, via, "enrolled the security key")

    def _submit(self, params: Params) -> str:
        raw = _hex(params.text("content"), "content")
        assertion = Assertion(
            _hex(params.text("auth_data"), "auth_data"),
            _hex(params.text("signature"), "signature"),
        )
        params.finish()
        try:
            content = EntryContent.decode(raw)
        except EntryFormatError as exc:
            raise ApiError(f"content is not an entry: {exc}") from exc
        self._admin.submit(content, assertion)
        return f"signed with the security key\n{self._admin.show()}"

    def _sign(self, roster: Roster, via: bytes | None, done: str) -> str:
        if via is not None:
            return self._admin.draft(roster, via).signed_bytes.hex()
        self._admin.publish(roster)
        return f"{done}\n{self._admin.show()}"


def _security_key(params: Params) -> bytes | None:
    value = params.optional_text(SECURITY_KEY_PARAM)
    params.finish()
    return None if value is None else _hex(value, SECURITY_KEY_PARAM)


def _hex(value: str, name: str) -> bytes:
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise ApiError(f"{name} must be hex") from exc
