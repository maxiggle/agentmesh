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
