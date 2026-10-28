import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "check_commit_msg.py"
    spec = importlib.util.spec_from_file_location("check_commit_msg", path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load()


@pytest.mark.parametrize(
    "message",
    [
        "feat(log): add canonical entry encoding",
        "fix: reject entries with a broken prev chain",
        "refactor(store)!: rename entries table",
        "docs: describe the roster entry\n\nWhy this matters.",
        "Merge branch 'feat/sync'",
    ],
)
def test_accepts_valid_messages(message: str) -> None:
    assert checker.problems(message) == []


@pytest.mark.parametrize(
    "message",
    [
        "add encoding",
        "feature(log): add encoding",
        "feat(log): Add encoding",
        "feat(log): add encoding.",
        "feat(Log): add encoding",
        "feat: " + "x" * 80,
        "feat: add encoding\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        "",
    ],
)
def test_rejects_invalid_messages(message: str) -> None:
    assert checker.problems(message)
