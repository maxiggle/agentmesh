import re
from pathlib import Path

import ehor


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", ehor.__version__)


def test_core_never_imports_the_near_package() -> None:
    package = Path(ehor.__file__).parent
    offenders = [
        str(path.relative_to(package))
        for path in sorted(package.rglob("*.py"))
        if "ehor_near" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
