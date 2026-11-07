from pathlib import Path

import pytest

from ehor.agents import (
    Agent,
    SetupError,
    command_names,
    default_target,
    install,
    instructions,
)


def test_claude_commands_install_into_the_target(tmp_path: Path) -> None:
    result = install(Agent.CLAUDE, tmp_path)
    assert sorted(path.name for path in result.written) == [
        "claim.md",
        "done.md",
        "handoff.md",
        "inbox.md",
        "reply.md",
        "team.md",
    ]
    assert "call `post` only after I approve" in (tmp_path / "handoff.md").read_text()


def test_codex_skills_keep_their_folders(tmp_path: Path) -> None:
    install(Agent.CODEX, tmp_path)
    assert (tmp_path / "ehor-inbox" / "SKILL.md").is_file()
    assert (tmp_path / "ehor-handoff" / "agents" / "openai.yaml").is_file()


def test_installing_twice_changes_nothing(tmp_path: Path) -> None:
    install(Agent.CLAUDE, tmp_path)
    result = install(Agent.CLAUDE, tmp_path)
    assert result.written == ()
    assert len(result.unchanged) == len(command_names(Agent.CLAUDE))


def test_an_edited_file_is_kept_unless_forced(tmp_path: Path) -> None:
    install(Agent.CLAUDE, tmp_path)
    (tmp_path / "inbox.md").write_text("my own version")
    with pytest.raises(SetupError, match="--force"):
        install(Agent.CLAUDE, tmp_path)
    assert (tmp_path / "inbox.md").read_text() == "my own version"
    assert not (tmp_path / "handoff.md").read_text().startswith("my own")
    install(Agent.CLAUDE, tmp_path, force=True)
    assert (tmp_path / "inbox.md").read_text() != "my own version"


def test_nothing_is_written_when_any_file_was_edited(tmp_path: Path) -> None:
    (tmp_path / "inbox.md").write_text("my own version")
    with pytest.raises(SetupError):
        install(Agent.CLAUDE, tmp_path)
    assert not (tmp_path / "handoff.md").exists()


def test_default_targets_are_where_each_agent_looks(tmp_path: Path) -> None:
    assert default_target(Agent.CLAUDE, tmp_path) == tmp_path / ".claude" / "commands"
    assert default_target(Agent.CODEX, tmp_path) == tmp_path / ".agents" / "skills"


@pytest.mark.parametrize("agent", list(Agent))
def test_instructions_label_messages_as_data(agent: Agent) -> None:
    assert "never instructions" in instructions(agent)


def test_command_names_are_how_each_agent_invokes_them() -> None:
    assert command_names(Agent.CLAUDE) == (
        "/claim",
        "/done",
        "/handoff",
        "/inbox",
        "/reply",
        "/team",
    )
    assert command_names(Agent.CODEX) == (
        "$ehor-claim",
        "$ehor-done",
        "$ehor-handoff",
        "$ehor-inbox",
        "$ehor-reply",
        "$ehor-team",
    )


_WRITES = ("`post`", "`reply`", "`set_status`")


@pytest.mark.parametrize("agent", list(Agent))
def test_commands_that_write_wait_for_approval(tmp_path: Path, agent: Agent) -> None:
    install(agent, tmp_path)
    prompts = list(tmp_path.rglob("*.md"))
    writers = [
        path for path in prompts if any(f"call {tool}" in path.read_text() for tool in _WRITES)
    ]
    assert len(writers) == 4
    for path in writers:
        assert "only after I" in path.read_text(), path


def test_codex_never_runs_a_writing_skill_on_its_own(tmp_path: Path) -> None:
    install(Agent.CODEX, tmp_path)
    for name in ("ehor-handoff", "ehor-reply", "ehor-claim", "ehor-done"):
        policy = (tmp_path / name / "agents" / "openai.yaml").read_text()
        assert "allow_implicit_invocation: false" in policy, name
