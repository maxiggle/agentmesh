from dataclasses import dataclass
from enum import Enum
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath


class SetupError(Exception):
    pass


class Agent(Enum):
    CLAUDE = "claude"
    CODEX = "codex"


@dataclass(frozen=True, slots=True)
class _Layout:
    bundle: str
    instructions: str
    target: PurePosixPath


_LAYOUTS = {
    Agent.CLAUDE: _Layout("commands", "CLAUDE.md", PurePosixPath(".claude/commands")),
    Agent.CODEX: _Layout("skills", "AGENTS.md", PurePosixPath(".agents/skills")),
}


@dataclass(frozen=True, slots=True)
class InstallResult:
    written: tuple[Path, ...]
    unchanged: tuple[Path, ...]


def default_target(agent: Agent, user_home: Path) -> Path:
    """Where `agent` looks for user-wide commands or skills."""
    return user_home / _LAYOUTS[agent].target


def command_names(agent: Agent) -> tuple[str, ...]:
    """How the user invokes each bundled command or skill, such as `/inbox` or `$ehor-inbox`."""
    bundle = _root(agent).joinpath(_LAYOUTS[agent].bundle)
    if agent is Agent.CLAUDE:
        return tuple(f"/{PurePosixPath(item.name).stem}" for item in _sorted(bundle))
    return tuple(f"${item.name}" for item in _sorted(bundle) if item.is_dir())


def instructions(agent: Agent) -> str:
    """The snippet that tells `agent` how to use ehor, for a project's CLAUDE.md or AGENTS.md."""
    return _root(agent).joinpath(_LAYOUTS[agent].instructions).read_text(encoding="utf-8")


def install(agent: Agent, target: Path, *, force: bool = False) -> InstallResult:
    """Copy `agent`'s bundled commands or skills into `target`.

    A file that already exists with other content is someone's edit, so nothing is
    written unless `force` is set.
    """
    bundle = dict(_walk(_root(agent).joinpath(_LAYOUTS[agent].bundle), PurePosixPath()))
    destinations = {target.joinpath(*relative.parts): data for relative, data in bundle.items()}
    edited = [path for path, data in destinations.items() if _differs(path, data)]
    if edited and not force:
        names = ", ".join(str(path) for path in edited)
        raise SetupError(
            f"{names} already exists with other content; rerun with --force to replace"
        )
    written: list[Path] = []
    unchanged: list[Path] = []
    for path, data in destinations.items():
        if path.is_file() and path.read_bytes() == data:
            unchanged.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(path)
    return InstallResult(tuple(written), tuple(unchanged))


def _root(agent: Agent) -> Traversable:
    return files(__name__).joinpath(agent.value)


def _walk(node: Traversable, relative: PurePosixPath) -> list[tuple[PurePosixPath, bytes]]:
    found: list[tuple[PurePosixPath, bytes]] = []
    for child in _sorted(node):
        if child.is_dir():
            found += _walk(child, relative / child.name)
        else:
            found.append((relative / child.name, child.read_bytes()))
    return found


def _sorted(node: Traversable) -> list[Traversable]:
    return sorted(node.iterdir(), key=lambda item: item.name)


def _differs(path: Path, data: bytes) -> bool:
    return path.exists() and (not path.is_file() or path.read_bytes() != data)
