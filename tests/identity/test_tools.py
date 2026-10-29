import sys
from pathlib import Path

import pytest

from agentmesh.identity import HardwareUnavailableError
from agentmesh.identity.tools import ToolError, ToolRunner, run_tool

PYTHON = sys.executable


def test_returns_stdout_and_passes_stdin() -> None:
    script = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()[::-1])"
    assert run_tool([PYTHON, "-c", script], stdin=b"abc") == b"cba"


def test_runs_in_the_given_folder_with_the_given_env(tmp_path: Path) -> None:
    script = "import os; print(os.getcwd()); print(os.environ['AGENTMESH_TEST'])"
    env = {"AGENTMESH_TEST": "set", "SYSTEMROOT": "C:\\Windows"}
    out = run_tool([PYTHON, "-c", script], cwd=tmp_path, env=env).decode().splitlines()
    assert Path(out[0]).resolve() == tmp_path.resolve()
    assert out[1] == "set"


def test_failure_reports_the_last_stderr_lines() -> None:
    stderr = "a\\nb\\n\\nkey not found\\nUnable to run\\n"
    script = f"import sys; sys.stderr.write('{stderr}'); sys.exit(4)"
    with pytest.raises(ToolError, match=r"failed: b / key not found / Unable to run$"):
        run_tool([PYTHON, "-c", script])


def test_failure_without_stderr_reports_the_exit_status() -> None:
    with pytest.raises(ToolError, match="exit status 3"):
        run_tool([PYTHON, "-c", "raise SystemExit(3)"])


def test_missing_tool_means_no_hardware() -> None:
    with pytest.raises(HardwareUnavailableError, match="not installed"):
        run_tool(["agentmesh-no-such-tool"])


def test_timeout() -> None:
    with pytest.raises(ToolError, match="timed out"):
        run_tool([PYTHON, "-c", "import time; time.sleep(5)"], timeout=0.2)


def test_run_tool_is_a_runner() -> None:
    runner: ToolRunner = run_tool
    assert runner is run_tool
