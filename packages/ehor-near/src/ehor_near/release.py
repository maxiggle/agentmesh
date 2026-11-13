from dataclasses import dataclass

from ehor.identity.base import KeyHandle, PresenceSigner
from ehor.identity.es256 import low_s, verify
from ehor_near.payload import KeyAction, encryption_key_payload, format_near, release_payload


class ReleaseSignError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseTerms:
    chain_id: str
    contract: str
    bounty_id: int
    claimant: str
    amount_yocto: int

    def payload(self) -> str:
        return release_payload(
            self.chain_id, self.contract, self.bounty_id, self.claimant, self.amount_yocto
        )

    def approval_reason(self) -> str:
        return (
            f"pay {format_near(self.amount_yocto)} NEAR to {self.claimant} "
            f"for ehor bounty {self.bounty_id}"
        )


@dataclass(frozen=True, slots=True)
class KeyChange:
    chain_id: str
    contract: str
    account: str
    nonce: int
    action: KeyAction
    key: bytes

    def payload(self) -> str:
        return encryption_key_payload(
            self.chain_id,
            self.contract,
            self.account,
            nonce=self.nonce,
            action=self.action,
            key=self.key,
        )

    def approval_reason(self) -> str:
        verb = "approve" if self.action is KeyAction.ADD else "remove"
        return f"{verb} ehor encryption key {self.key.hex()[:16]} for {self.account}"


def sign_release(signer: PresenceSigner, handle: KeyHandle, terms: ReleaseTerms) -> str:
    """Sign `terms` with the release key and return the hex signature `release` takes.

    The release key needs the person at the machine to approve each signature. Only
    `ehor pay` may call this, with terms read from the chain.
    """
    return _sign(signer, handle, terms.payload(), terms.approval_reason())


def sign_key_change(signer: PresenceSigner, handle: KeyHandle, change: KeyChange) -> str:
    """Sign an encryption key change with the release key, for `ehor near keys` only."""
    return _sign(signer, handle, change.payload(), change.approval_reason())


def _sign(signer: PresenceSigner, handle: KeyHandle, payload: str, reason: str) -> str:
    message = payload.encode("utf-8")
    signature = low_s(signer.sign_with_presence(handle, message, reason))
    if not verify(signer.public_key(handle), signature, message):
        raise ReleaseSignError("the release key returned a signature that does not verify")
    return signature.hex()
