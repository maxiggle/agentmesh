from dataclasses import dataclass

from ehor.escrow.payload import release_payload
from ehor.identity.base import KeyHandle, Signer
from ehor.identity.es256 import low_s, verify


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


def sign_release(signer: Signer, handle: KeyHandle, terms: ReleaseTerms) -> str:
    """Sign `terms` with the device key and return the hex signature the contract's `release` takes.

    Only `ehor pay` may call this, after a human has confirmed terms read from the chain.
    """
    message = terms.payload().encode("utf-8")
    signature = low_s(signer.sign(handle, message))
    if not verify(signer.public_key(handle), signature, message):
        raise ReleaseSignError("the device key returned a signature that does not verify")
    return signature.hex()
