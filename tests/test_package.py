import re

import ehor


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", ehor.__version__)
