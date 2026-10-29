from agentmesh.log.roster import Trust
from agentmesh.roster.book import RosterBook
from agentmesh.service.messenger import Identity


class RosterDirectory:
    """Names and identity from the current roster.

    Until this device is on the roster it has its configured handle but no
    roles or channels, so nothing is routed to it as a member yet.
    """

    def __init__(self, book: RosterBook, author: bytes, handle: str) -> None:
        self._book = book
        self._author = author
        self._handle = handle

    def me(self) -> Identity:
        member = self._book.member_for_key(self._author)
        if member is None:
            return Identity(self._handle, (), ())
        return Identity(member.handle, member.roles, member.channels)

    def name(self, author: bytes) -> str | None:
        member = self._book.member_for_key(author)
        return None if member is None else member.handle

    def known_authors(self) -> tuple[bytes, ...]:
        roster = self._book.current
        if roster is None:
            return ()
        return tuple(
            keys[0]
            for member in roster.members
            if (keys := [d.key for d in member.devices if d.trust is not Trust.SECURITY_KEY])
        )
