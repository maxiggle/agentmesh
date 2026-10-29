import re

CONTROL_CHANNEL = "_team"

HANDLE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
ROLE = re.compile(r"@[a-z0-9][a-z0-9-]{0,31}")
TEAM_CHANNEL = re.compile(r"#[a-z0-9][a-z0-9-]{0,31}")
DM_CHANNEL = re.compile(rf"dm:({HANDLE.pattern})\+({HANDLE.pattern})")


def dm_channel(a: str, b: str) -> str:
    first, second = sorted((a, b))
    return f"dm:{first}+{second}"


def dm_members(channel: str) -> tuple[str, str] | None:
    match = DM_CHANNEL.fullmatch(channel)
    if match is None or match.group(1) >= match.group(2):
        return None
    return match.group(1), match.group(2)


ADDRESS_MAX = 255
_MAX_PORT = 65535


def parse_address(address: str) -> tuple[str, int] | None:
    """Split a `host:port` address, or return None if it is not one."""
    if len(address) > ADDRESS_MAX:
        return None
    host, _, port = address.rpartition(":")
    if not host or not port.isdigit() or not 1 <= int(port) <= _MAX_PORT:
        return None
    return host.strip("[]"), int(port)
