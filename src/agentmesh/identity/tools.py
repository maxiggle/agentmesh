import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from agentmesh.identity.base import HardwareUnavailableError, IdentityError

DEFAULT_TIMEOUT = 30.0
_REASON_LINES = 3


class ToolError(IdentityError):
    pass


class ToolRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        stdin: bytes = b"",
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> bytes: ...


def run_tool(
    args: Sequence[str],
    *,
    stdin: bytes = b"",
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bytes:
    """Run a key tool and return its stdout, turning any failure into an IdentityError."""
    name = Path(args[0]).name
    try:
        result = subprocess.run(  # noqa: S603 - fixed tool names, never shell input
            list(args),
            input=stdin,
            capture_output=True,
            cwd=cwd,
            env=None if env is None else dict(env),
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HardwareUnavailableError(f"{name} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{name} timed out after {timeout:g}s") from exc
    if result.returncode != 0:
        lines = [line.strip() for line in result.stderr.decode(errors="replace").splitlines()]
        details = [line for line in lines if line][-_REASON_LINES:]
        reason = " / ".join(details) if details else f"exit status {result.returncode}"
        raise ToolError(f"{name} failed: {reason}")
    return result.stdout
