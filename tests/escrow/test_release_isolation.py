"""Only the human-run CLI may reach the release signer or the presence key behind it.

See docs/escrow.md, safety invariants. An agent that could trigger a presence prompt could time
it to trick the person into approving, so it must not reach the presence signer either.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = SRC / "ehor"
SIGNER_MODULE = "ehor.escrow.release"
SIGNER_NAMES = {"release", "sign_release"}
PRESENCE_SIGNER = "SecureEnclavePresenceSigner"
ALLOWED = {"ehor.near_cli", SIGNER_MODULE}
PRESENCE_ALLOWED = {"ehor.near_cli", "ehor.identity.secure_enclave"}


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def reaches_signer(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith(SIGNER_MODULE)]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith(SIGNER_MODULE):
                found.append(node.module)
            elif node.module == "ehor.escrow":
                found += [f"ehor.escrow.{a.name}" for a in node.names if a.name in SIGNER_NAMES]
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and SIGNER_MODULE in node.value
        ):
            found.append(repr(node.value))
    return found


@pytest.mark.parametrize(
    "source",
    [
        "import ehor.escrow.release",
        "from ehor.escrow.release import sign_release",
        "from ehor.escrow import release",
        "from ehor.escrow import sign_release",
        "import importlib\nimportlib.import_module('ehor.escrow.release')",
        "def f() -> None:\n    from ehor.escrow.release import sign_release",
    ],
)
def test_scanner_catches_every_way_to_reach_the_signer(source: str) -> None:
    assert reaches_signer(source)


def test_scanner_ignores_other_escrow_modules() -> None:
    assert not reaches_signer("from ehor.escrow.payload import release_payload")
    assert not reaches_signer("from ehor.escrow import payload, client")


def uses_presence_signer(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names = (
            [a.name for a in node.names] if isinstance(node, ast.Import | ast.ImportFrom) else []
        )
        if isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names = [node.value]
        found += [name for name in names if PRESENCE_SIGNER in name or "create-presence" in name]
    return found


@pytest.mark.parametrize(
    "source",
    [
        "from ehor.identity.secure_enclave import SecureEnclavePresenceSigner",
        "from ehor.identity import secure_enclave\nsecure_enclave.SecureEnclavePresenceSigner",
        "getattr(module, 'SecureEnclavePresenceSigner')",
        "helper(['create-presence'])",
    ],
)
def test_scanner_catches_every_way_to_reach_the_presence_signer(source: str) -> None:
    assert uses_presence_signer(source)


def test_only_the_near_commands_use_the_presence_signer() -> None:
    offenders = {
        _module_name(path): hits
        for path in sorted(PACKAGE.rglob("*.py"))
        if _module_name(path) not in PRESENCE_ALLOWED
        and (hits := uses_presence_signer(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_only_the_near_commands_import_the_release_signer() -> None:
    offenders = {
        _module_name(path): hits
        for path in sorted(PACKAGE.rglob("*.py"))
        if _module_name(path) not in ALLOWED
        and (hits := reaches_signer(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_loading_the_core_cli_mcp_server_and_daemon_does_not_load_the_signer() -> None:
    probe = (
        "import sys, ehor.cli, ehor.mcp.server, ehor.service.daemon\n"
        f"print({SIGNER_MODULE!r} in sys.modules)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code, no user input
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
