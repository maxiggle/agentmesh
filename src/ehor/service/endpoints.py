from ehor.log.endpoint_announce import EndpointAnnounce, EndpointAnnounceError
from ehor.log.entry import Kind
from ehor.store.base import Store


class EndpointDirectory:
    """Each device's current iroh endpoint ID: the one in its latest endpoint_announce."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def endpoints(self) -> dict[bytes, bytes]:
        latest: dict[bytes, tuple[int, bytes]] = {}
        for arrival in self._store.arrivals(kinds=[Kind.ENDPOINT_ANNOUNCE]):
            entry = arrival.entry
            try:
                iroh = EndpointAnnounce.decode(entry.content.body).iroh
            except EndpointAnnounceError:
                continue
            if entry.seq >= latest.get(entry.author, (0, b""))[0]:
                latest[entry.author] = (entry.seq, iroh)
        return {author: iroh for author, (_, iroh) in latest.items()}
