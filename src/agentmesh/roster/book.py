from collections.abc import Iterable

from agentmesh.log.authorize import Decision, Verdict
from agentmesh.log.entry import Entry, Kind
from agentmesh.log.envelope import Encryption, Envelope, EnvelopeError
from agentmesh.log.names import CONTROL_CHANNEL, dm_members
from agentmesh.log.roster import Member, Roster, RosterError


class RosterBook:
    """Tracks the roster chain from the pinned genesis and authorizes entries against it."""

    def __init__(self, genesis_id: bytes) -> None:
        self._genesis_id = genesis_id
        self._rosters: dict[bytes, Roster] = {}
        self._children: dict[bytes, list[bytes]] = {}
        self._current_id: bytes | None = None
        self._forked = False

    @property
    def genesis_id(self) -> bytes:
        return self._genesis_id

    @property
    def current(self) -> Roster | None:
        return None if self._current_id is None else self._rosters[self._current_id]

    @property
    def current_id(self) -> bytes | None:
        return self._current_id

    @property
    def forked(self) -> bool:
        return self._forked

    def load(self, entries: Iterable[Entry]) -> None:
        pending = [entry for entry in entries if entry.content.kind is Kind.ROSTER]
        progress = True
        while pending and progress:
            progress = False
            for entry in list(pending):
                if self.authorize(entry).verdict is not Verdict.WAIT:
                    pending.remove(entry)
                    progress = self.accepted(entry) or progress

    def member_for_key(self, key: bytes) -> Member | None:
        current = self.current
        return None if current is None else current.member_for_key(key)

    def may_receive_logs(self, key: bytes) -> bool:
        """Members and the team's relays get full logs in sync; anyone else only rosters."""
        current = self.current
        if current is None:
            return False
        return current.member_for_key(key) is not None or current.relay_for_key(key) is not None

    def authorize(self, entry: Entry) -> Decision:
        if entry.content.kind is Kind.ROSTER:
            return self._authorize_roster(entry)
        current = self.current
        if current is None:
            return Decision.wait("no roster yet")
        member = current.member_for_key(entry.author)
        if member is None:
            return Decision.wait("author is not on the roster")
        if current.require_encryption and _is_plaintext(entry):
            return Decision.deny("this team requires encrypted messages")
        return self._authorize_channel(member, entry.content.channel)

    @staticmethod
    def _authorize_channel(member: Member, channel: str) -> Decision:
        members = dm_members(channel)
        if channel == CONTROL_CHANNEL:
            allowed = True
        elif members is not None:
            allowed = member.handle in members
        else:
            allowed = member.can_use(channel)
        if allowed:
            return Decision.allow()
        return Decision.wait(f"{member.handle} may not post in {channel}")

    def accepted(self, entry: Entry) -> bool:
        if entry.content.kind is not Kind.ROSTER or entry.id in self._rosters:
            return False
        if self._authorize_roster(entry).verdict is not Verdict.ALLOW:
            return False
        roster = Roster.decode(entry.content.body)
        self._rosters[entry.id] = roster
        if roster.previous is not None:
            self._children.setdefault(roster.previous, []).append(entry.id)
        before = (self._current_id, self._forked)
        self._recompute()
        return (self._current_id, self._forked) != before

    def _authorize_roster(self, entry: Entry) -> Decision:
        try:
            roster = Roster.decode(entry.content.body)
        except RosterError as exc:
            return Decision.deny(f"bad roster: {exc}")
        if roster.previous is None:
            return self._authorize_genesis(entry, roster)
        previous = self._rosters.get(roster.previous)
        if previous is None:
            return Decision.wait("previous roster not known yet")
        if roster.version != previous.version + 1:
            return Decision.deny("roster version must follow its previous roster")
        if not previous.is_admin_device(entry.author):
            return Decision.deny("roster must be signed by an admin of the previous roster")
        return Decision.allow()

    def _authorize_genesis(self, entry: Entry, roster: Roster) -> Decision:
        if entry.id != self._genesis_id:
            return Decision.deny("not the pinned genesis roster")
        if not roster.is_admin_device(entry.author):
            return Decision.deny("genesis roster must be signed by one of its admins")
        return Decision.allow()

    def _recompute(self) -> None:
        if self._genesis_id not in self._rosters:
            self._current_id, self._forked = None, False
            return
        node, forked = self._genesis_id, False
        while children := self._children.get(node):
            if len(children) == 1:
                node = children[0]
                continue
            continued = [child for child in children if self._children.get(child)]
            if len(continued) == 1:
                node = continued[0]
                continue
            forked = True
            break
        self._current_id, self._forked = node, forked


def _is_plaintext(entry: Entry) -> bool:
    if entry.content.kind not in (Kind.MESSAGE, Kind.STATUS):
        return False
    try:
        return Envelope.decode(entry.content.body).enc is Encryption.NONE
    except EnvelopeError:
        return False
