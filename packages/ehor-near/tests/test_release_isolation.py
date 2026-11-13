"""Only the human-run CLI may reach the release signer or the presence key behind it.

See docs/escrow.md, safety invariants. An agent that could trigger a presence prompt could time
it to trick the person into approving, so it must not reach the presence signer either.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import ehor
import ehor_near

PACKAGES = [Path(ehor.__file__).parent, Path(ehor_near.__file__).parent]
SIGNER_MODULE = "ehor_near.release"
SIGNER_NAMES = {"release", "sign_release", "sign_key_change"}
PRESENCE_SIGNER = "SecureEnclavePresenceSigner"
ALLOWED = {"ehor_near.cli", SIGNER_MODULE}
PRESENCE_ALLOWED = {"ehor_near.cli", "ehor.identity.secure_enclave"}


def _module_name(package: Path, path: Path) -> str:
    parts = path.relative_to(package.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _sources() -> dict[str, str]:
    return {
        _module_name(package, path): path.read_text(encoding="utf-8")
        for package in PACKAGES
        for path in sorted(package.rglob("*.py"))
    }


def reaches_signer(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith(SIGNER_MODULE)]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith(SIGNER_MODULE):
                found.append(node.module)
            elif node.module == "ehor_near":
                found += [f"ehor_near.{a.name}" for a in node.names if a.name in SIGNER_NAMES]
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
        "import ehor_near.release",
        "from ehor_near.release import sign_release",
        "from ehor_near import release",
        "from ehor_near import sign_release",
        "import importlib\nimportlib.import_module('ehor_near.release')",
        "def f() -> None:\n    from ehor_near.release import sign_release",
    ],
)
def test_scanner_catches_every_way_to_reach_the_signer(source: str) -> None:
    assert reaches_signer(source)


def test_scanner_ignores_other_escrow_modules() -> None:
    assert not reaches_signer("from ehor_near.payload import release_payload")
    assert not reaches_signer("from ehor_near import payload, client")


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
        module: hits
        for module, source in _sources().items()
        if module not in PRESENCE_ALLOWED and (hits := uses_presence_signer(source))
    }
    assert offenders == {}


def test_only_the_near_commands_import_the_release_signer() -> None:
    offenders = {
        module: hits
        for module, source in _sources().items()
        if module not in ALLOWED and (hits := reaches_signer(source))
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


def test_loading_the_agents_bounty_tools_does_not_load_the_signer() -> None:
    probe = (
        "import sys, ehor.cli\n"
        "plugins = ehor.cli._tool_plugins()\n"
        "print(sorted(type(plugin).__name__ for plugin in plugins))\n"
        f"print({SIGNER_MODULE!r} in sys.modules)"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and code, no user input
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.splitlines() == ["['NearTools']", "False"]


def test_the_scan_covers_both_packages() -> None:
    modules = _sources()
    assert {"ehor.mcp.server", "ehor_near.cli", SIGNER_MODULE} <= modules.keys()
