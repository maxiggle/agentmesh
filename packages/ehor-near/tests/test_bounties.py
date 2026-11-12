import asyncio
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from ehor.log.account_announce import AccountAnnounce
from ehor.service.device import Device
from ehor.service.messenger import MessengerError
from ehor_near.bounties import NEAR_SYSTEM, POST_GAS, STEP_GAS, BountyDesk, BountyError
from ehor_near.client import DEFAULT_GAS, ContractError
from ehor_near.config import NearConfig
from ehor_near.pay import PayError

CONTRACT = "ehor-escrow.testnet"
GEF = "gef.testnet"
TOBI = "tobi.testnet"
EVE = "eve.testnet"
NOW = 1_794_400_000
NS = 10**9
PASSPHRASE = b"test passphrase"
KEYS = {GEF: b"\x02" + b"\x01" * 32, TOBI: b"\x03" + b"\x02" * 32, EVE: b"\x02" + b"\x03" * 32}


@dataclass
class Chain:
    """Enough of ehor-escrow to check what the bounty tools ask of it."""

    bounties: dict[int, dict[str, object]] = field(default_factory=dict)
    calls: list[tuple[str, str, dict[str, object], int]] = field(default_factory=list)
    release_keys: dict[str, bytes] = field(default_factory=lambda: dict(KEYS))

    def client(self, account: str) -> "AccountClient":
        return AccountClient(self, account)

    def add(self, sender: str, message_id: str, claimants: Sequence[str], amount: int) -> int:
        bounty_id = len(self.bounties) + 1
        self.bounties[bounty_id] = {
            "id": bounty_id,
            "sender": sender,
            "amount": str(amount),
            "message_id": message_id,
            "allowed_claimants": list(claimants),
            "claimant": None,
            "status": "Open",
            "deadline": str((NOW + 3600) * NS),
            "created": str(NOW * NS),
        }
        return bounty_id


@dataclass
class AccountClient:
    chain: Chain
    account: str

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def view(self, contract: str, method: str, args: Mapping[str, object]) -> object:
        assert contract == CONTRACT
        if method == "get_account":
            key = self.chain.release_keys.get(str(args["account_id"]))
            return None if key is None else {"device_key": key.hex()}
        assert method == "get_bounty"
        return self.chain.bounties.get(int(str(args["bounty_id"])))

    async def call(
        self,
        contract: str,
        method: str,
        args: Mapping[str, object],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> object:
        assert contract == CONTRACT
        assert deposit_yocto == 0
        self.chain.calls.append((self.account, method, dict(args), gas))
        if method == "post_bounty":
            claimants = args["allowed_claimants"]
            assert isinstance(claimants, list)
            bounty_id = self.chain.add(
                self.account, str(args["message_id"]), claimants, int(str(args["amount"]))
            )
            self.chain.bounties[bounty_id]["deadline"] = args["deadline"]
            return bounty_id
        bounty = self.chain.bounties[int(str(args["bounty_id"]))]
        if method == "claim":
            claimants = bounty["allowed_claimants"]
            assert isinstance(claimants, list)
            if bounty["status"] != "Open" or self.account not in claimants:
                raise ContractError("ERR_NOT_ALLOWED")
            bounty.update(claimant=self.account, status="Claimed")
        elif method == "mark_done":
            if bounty["claimant"] != self.account:
                raise ContractError("ERR_NOT_CLAIMANT")
            bounty["status"] = "Done"
        else:
            raise AssertionError(method)
        return None

    async def access_key(self, account: str, public_key: str) -> object | None:
        raise AssertionError("bounty tools never look up access keys")

    async def add_function_call_key(
        self, public_key: str, contract: str, methods: Sequence[str], allowance_yocto: int
    ) -> None:
        raise AssertionError("bounty tools never add keys")


@dataclass
class Team:
    gef: Device
    tobi: Device
    gef_home: Path
    tobi_home: Path
    chain: Chain

    def desk(self, who: str) -> BountyDesk:
        device, home, account = (
            (self.gef, self.gef_home, GEF) if who == "gef" else (self.tobi, self.tobi_home, TOBI)
        )
        return BountyDesk(
            home,
            NearConfig(account, CONTRACT),
            lambda: self.chain.client(account),
            messages=device.messenger,
            accounts=device,
            clock=lambda: float(NOW),
        )

    def settle(self) -> None:
        for _ in range(3):
            self.gef.sync()
            self.tobi.sync()

    def announce(self, device: Device, account: str, key: bytes | None = None) -> None:
        device.announce_account(AccountAnnounce(NEAR_SYSTEM, account, key or KEYS[account]))
        self.settle()

    def request(self, to: str = "tobi", kind: str = "feature_request") -> str:
        entry = self.gef.messenger.post([to], kind, "Add CSV export")
        self.settle()
        return entry.id.hex()


@pytest.fixture
def team() -> Iterator[Team]:
    with tempfile.TemporaryDirectory(prefix="ehor") as directory:
        root = Path(directory)
        shared = root / "shared"
        gef = Device.create_team(root / "gef", "gef", PASSPHRASE, exchange=shared)
        tobi = Device.join(root / "tobi", gef.invite, "tobi", PASSPHRASE, exchange=shared)
        gef.call("roster.add", {"handle": "tobi", "key": tobi.author.hex()})
        built = Team(gef, tobi, root / "gef", root / "tobi", Chain())
        built.settle()
        built.announce(gef, GEF)
        built.announce(tobi, TOBI)
        yield built
        gef.close()
        tobi.close()


def test_agents_post_claim_and_finish_a_bounty(team: Team) -> None:
    message_id = team.request()
    gef, tobi = team.desk("gef"), team.desk("tobi")

    posted = asyncio.run(gef.post(message_id[:8], "0.1"))
    assert posted == (
        f"posted bounty 1: 0.1 NEAR for tobi by 2026-11-12 12:26 UTC ({TOBI}, read from the chain)"
    )
    assert team.chain.calls == [
        (
            GEF,
            "post_bounty",
            {
                "amount": str(10**23),
                "message_id": message_id,
                "allowed_claimants": [TOBI],
                "deadline": str((NOW + 24 * 3600) * NS),
            },
            POST_GAS,
        )
    ]
    team.settle()
    replies = team.tobi.messenger.read(message_id[:8]).replies
    assert [reply.body.summary for reply in replies] == [
        "NEAR bounty 1: 0.1 NEAR for tobi by 2026-11-12 12:26 UTC"
    ]

    claimed = asyncio.run(tobi.claim(message_id[:8], 1))
    assert claimed == f"claimed bounty 1: 0.1 NEAR from {GEF}, due 2026-11-12 12:26 UTC"
    done = asyncio.run(tobi.finish(message_id[:8], 1))
    assert done == "bounty 1 is done; gef can pay it with `ehor pay 1`"
    assert [(call[0], call[1], call[3]) for call in team.chain.calls[1:]] == [
        (TOBI, "claim", STEP_GAS),
        (TOBI, "mark_done", STEP_GAS),
    ]
    team.settle()
    assert str(team.gef.messenger.read(message_id[:8]).message.status) == "done"

    assert asyncio.run(gef.overview()) == (
        f"1 · 0.1 NEAR · Done · for {TOBI} · pay with `ehor pay 1` · message {message_id[:8]}"
    )
    assert asyncio.run(tobi.overview()) == (
        f"1 · 0.1 NEAR · Done · from {GEF} · message {message_id[:8]}"
    )


def test_a_bounty_posted_by_another_account_cannot_be_claimed(team: Team) -> None:
    message_id = team.request()
    forged = team.chain.add(EVE, message_id, [TOBI], 10**24)
    with pytest.raises(BountyError, match=f"posted by {EVE}, not {GEF}"):
        asyncio.run(team.desk("tobi").claim(message_id[:8], forged))
    assert team.chain.calls == []


def test_a_bounty_for_another_message_cannot_be_claimed(team: Team) -> None:
    message_id = team.request()
    other = team.chain.add(GEF, "ab" * 32, [TOBI], 10**23)
    with pytest.raises(BountyError, match="not linked to this message"):
        asyncio.run(team.desk("tobi").claim(message_id[:8], other))
    assert team.chain.calls == []


def test_a_bounty_offered_to_someone_else_cannot_be_claimed(team: Team) -> None:
    message_id = team.request()
    bounty = team.chain.add(GEF, message_id, [EVE], 10**23)
    with pytest.raises(BountyError, match=f"not offered to {TOBI}"):
        asyncio.run(team.desk("tobi").claim(message_id[:8], bounty))
    assert team.chain.calls == []


def test_a_claimed_bounty_cannot_be_claimed_again(team: Team) -> None:
    message_id = team.request()
    bounty = team.chain.add(GEF, message_id, [TOBI], 10**23)
    asyncio.run(team.desk("tobi").claim(message_id[:8], bounty))
    with pytest.raises(BountyError, match="is Claimed, not Open"):
        asyncio.run(team.desk("tobi").claim(message_id[:8], bounty))


def test_only_the_claimant_can_finish(team: Team) -> None:
    message_id = team.request()
    bounty = team.chain.add(GEF, message_id, [TOBI], 10**23)
    with pytest.raises(BountyError, match=f"not claimed by {TOBI}"):
        asyncio.run(team.desk("tobi").finish(message_id[:8], bounty))
    assert team.chain.calls == []


def test_the_requester_cannot_claim_their_own_request(team: Team) -> None:
    message_id = team.request()
    bounty = team.chain.add(GEF, message_id, [TOBI], 10**23)
    with pytest.raises(BountyError, match="that request is yours"):
        asyncio.run(team.desk("gef").claim(message_id[:8], bounty))


def test_a_bounty_goes_only_on_your_own_feature_request(team: Team) -> None:
    request = team.request()
    note = team.request(kind="fyi")
    with pytest.raises(BountyError, match="request you sent"):
        asyncio.run(team.desk("tobi").post(request[:8], "0.1"))
    with pytest.raises(BountyError, match="only go on a feature_request"):
        asyncio.run(team.desk("gef").post(note[:8], "0.1"))
    assert team.chain.calls == []


def test_a_bounty_needs_one_person_to_pay(team: Team) -> None:
    to_channel = team.request(to="#general")
    with pytest.raises(BountyError, match="not addressed to one person"):
        asyncio.run(team.desk("gef").post(to_channel[:8], "0.1"))
    with pytest.raises(BountyError, match="ada has not announced a NEAR account"):
        asyncio.run(team.desk("gef").post(to_channel[:8], "0.1", claimant="ada"))
    assert team.chain.calls == []


def test_an_account_announced_with_another_release_key_is_refused(team: Team) -> None:
    message_id = team.request()
    team.announce(team.tobi, EVE, KEYS[TOBI])
    with pytest.raises(BountyError, match=f"another release key for {EVE}"):
        asyncio.run(team.desk("gef").post(message_id[:8], "0.1"))
    assert team.chain.calls == []


def test_an_account_not_registered_on_the_contract_is_refused(team: Team) -> None:
    message_id = team.request()
    del team.chain.release_keys[TOBI]
    with pytest.raises(PayError, match=f"{TOBI} is not registered"):
        asyncio.run(team.desk("gef").post(message_id[:8], "0.1"))
    assert team.chain.calls == []


def test_a_claim_follows_the_senders_latest_announcement(team: Team) -> None:
    message_id = team.request()
    bounty = team.chain.add(GEF, message_id, [TOBI], 10**23)
    team.chain.release_keys[EVE] = KEYS[GEF]
    team.announce(team.gef, EVE, KEYS[GEF])
    with pytest.raises(BountyError, match=f"posted by {GEF}, not {EVE}"):
        asyncio.run(team.desk("tobi").claim(message_id[:8], bounty))
    assert team.chain.calls == []


@pytest.mark.parametrize("hours", [0, 721])
def test_deadlines_are_bounded(team: Team, hours: int) -> None:
    message_id = team.request()
    with pytest.raises(BountyError, match="hours must be between"):
        asyncio.run(team.desk("gef").post(message_id[:8], "0.1", hours=hours))
    assert team.chain.calls == []


def test_an_unknown_message_is_reported(team: Team) -> None:
    with pytest.raises(MessengerError):
        asyncio.run(team.desk("gef").post("ffffffff", "0.1"))


def test_no_bounties_yet(team: Team) -> None:
    assert asyncio.run(team.desk("gef").overview()) == "no bounties yet"
