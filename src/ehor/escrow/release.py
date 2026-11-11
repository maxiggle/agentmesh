from dataclasses import dataclass
from decimal import Decimal, localcontext

from ehor.escrow.payload import release_payload
from ehor.identity.base import KeyHandle, PresenceSigner
from ehor.identity.es256 import low_s, verify


class ReleaseSignError(Exception):
    pass


def format_near(amount_yocto: int) -> str:
    with localcontext(prec=80):
        near = Decimal(amount_yocto).scaleb(-24).normalize()
    return f"{near:f}"


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


def sign_release(signer: PresenceSigner, handle: KeyHandle, terms: ReleaseTerms) -> str:
    """Sign `terms` with the release key and return the hex signature `release` takes.

    The release key needs the person at the machine to approve each signature. Only
    `ehor pay` may call this, with terms read from the chain.
    """
    message = terms.payload().encode("utf-8")
    raw = signer.sign_with_presence(handle, message, terms.approval_reason())
    signature = low_s(raw)
    if not verify(signer.public_key(handle), signature, message):
        raise ReleaseSignError("the release key returned a signature that does not verify")
    return signature.hex()
