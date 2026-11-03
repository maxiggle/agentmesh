import contextlib
import importlib
import os
import secrets
from pathlib import Path

SECRET_SIZE = 32
_FILE_MODE = 0o600


class IrohKeyError(ValueError):
    pass


def load_or_create_secret(path: Path) -> bytes:
    """Read this device's iroh secret, creating it with owner-only access on first use.

    A plain file is enough: it only fixes the device's network address. The sync
    handshake, not this key, authenticates the device.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _create(path)
    return _read(path)


def endpoint_id_for(secret: bytes) -> bytes | None:
    """The iroh endpoint ID this secret gives, or None when iroh is not installed."""
    try:
        iroh = importlib.import_module("iroh")
    except ImportError:
        return None
    endpoint: bytes = iroh.SecretKey.from_bytes(secret).public().to_bytes()
    return endpoint


def stored_endpoint_id(path: Path) -> bytes | None:
    """The endpoint ID of an existing iroh secret, without creating one."""
    if not path.exists():
        return None
    return endpoint_id_for(_read(path))


def _read(path: Path) -> bytes:
    secret = path.read_bytes()
    if len(secret) != SECRET_SIZE:
        raise IrohKeyError(
            f"{path} is not a {SECRET_SIZE}-byte iroh key; delete it to get a new address"
        )
    return secret


def _create(path: Path) -> None:
    """Publish a fully written key under its final name, so a crash never leaves a short one."""
    temp = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(os.urandom(SECRET_SIZE))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            pass
        except OSError:
            if not path.exists():
                temp.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()
