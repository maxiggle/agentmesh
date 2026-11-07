from pathlib import Path

import pytest

from ehor.agents import Agent, SetupError, default_target, install, instructions


def test_claude_commands_install_into_the_target(tmp_path: Path) -> None:
    result = install(Agent.CLAUDE, tmp_path)
    assert sorted(path.name for path in result.written) == ["handoff.md", "inbox.md"]
    assert "call `post` only after I approve" in (tmp_path / "handoff.md").read_text()


def test_codex_skills_keep_their_folders(tmp_path: Path) -> None:
    install(Agent.CODEX, tmp_path)
    assert (tmp_path / "ehor-inbox" / "SKILL.md").is_file()
    assert (tmp_path / "ehor-handoff" / "agents" / "openai.yaml").is_file()


def test_installing_twice_changes_nothing(tmp_path: Path) -> None:
    install(Agent.CLAUDE, tmp_path)
    result = install(Agent.CLAUDE, tmp_path)
    assert result.written == ()
    assert len(result.unchanged) == 2


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
