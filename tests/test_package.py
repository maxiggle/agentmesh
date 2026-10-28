import re

import agentmesh


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", agentmesh.__version__)
