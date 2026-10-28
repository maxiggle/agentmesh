import json
from pathlib import Path

import pytest

from agentmesh.cli import main


@pytest.fixture(autouse=True)
def passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTMESH_PASSPHRASE", "test passphrase")


def test_init_creates_a_device(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "gef"
    code = main(
        [
            "--home",
            str(home),
            "init",
            "--handle",
            "gef",
            "--role",
            "@backend",
            "--channel",
            "#backend",
            "--exchange",
            str(tmp_path / "shared"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"created device gef in {home}")
    config = json.loads((home / "config.json").read_text())
    assert config["roles"] == ["@backend"]
    assert config["channels"] == ["#backend"]
    assert config["exchange"] == str((tmp_path / "shared").resolve())
    assert config["team"] in out


def test_init_joins_an_existing_team(tmp_path: Path) -> None:
    team = "ab" * 16
    assert main(["--home", str(tmp_path), "init", "--handle", "tobi", "--team", team]) == 0
    assert json.loads((tmp_path / "config.json").read_text())["team"] == team


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["init", "--handle", "Gef"], "invalid handle"),
        (["init", "--handle", "gef", "--team", "xyz"], "team id must be hex"),
        (["init", "--handle", "gef", "--team", "abcd"], "team id must be 16 bytes"),
        (["init", "--handle", "gef", "--role", "backend"], "invalid role"),
        (["digest"], "not running"),
    ],
)
def test_errors_are_short(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], error: str
) -> None:
    assert main(["--home", str(tmp_path), *args]) == 1
    assert error in capsys.readouterr().err


def test_init_refuses_an_existing_device(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--home", str(tmp_path), "init", "--handle", "gef"])
    assert main(["--home", str(tmp_path), "init", "--handle", "gef"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_serve_needs_a_device(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--home", str(tmp_path), "serve"]) == 1
    assert "agentmesh init" in capsys.readouterr().err
