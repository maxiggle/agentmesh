from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agentmesh.log.entry import Entry


class Verdict(StrEnum):
    ALLOW = "allow"
    WAIT = "wait"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    reason: str = ""

    @classmethod
    def allow(cls) -> "Decision":
        return cls(Verdict.ALLOW)

    @classmethod
    def wait(cls, reason: str) -> "Decision":
        return cls(Verdict.WAIT, reason)

    @classmethod
    def deny(cls, reason: str) -> "Decision":
        return cls(Verdict.DENY, reason)


class Authorizer(Protocol):
    """Protocol §5 step 8: may this author write this entry?"""

    def authorize(self, entry: Entry) -> Decision: ...

    def accepted(self, entry: Entry) -> bool:
        """Record an authorized entry; return True if that changed what is authorized."""
        ...
