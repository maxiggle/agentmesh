import argparse
import asyncio
import contextlib
import getpass
import logging
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

from agentmesh.identity import IdentityError
from agentmesh.mcp.server import build_server
from agentmesh.service.config import ConfigError, default_home
from agentmesh.service.daemon import DEFAULT_SYNC_INTERVAL, Daemon
from agentmesh.service.device import Device
from agentmesh.service.rpc import RpcClient, RpcError, default_endpoint

PASSPHRASE_ENV = "AGENTMESH_PASSPHRASE"  # noqa: S105 - env var name, not a secret


class CliError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.run(args))
    except (CliError, ConfigError, IdentityError, RpcError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentmesh", description="Signed messaging between Claude agents."
    )
    parser.add_argument("--home", type=Path, default=None, help="device folder")
    commands = parser.add_subparsers(required=True, metavar="command")

    init = commands.add_parser("init", help="start a new team on this device")
    init.add_argument("--handle", required=True, help="your short name, e.g. gef")
    init.add_argument("--team-name", default="", help="display name for the team")
    init.add_argument("--exchange", type=Path, help="shared folder for swapping entries")
    init.set_defaults(run=_init)

    serve = commands.add_parser("serve", help="run the background service")
    serve.add_argument("--interval", type=float, default=DEFAULT_SYNC_INTERVAL)
    serve.set_defaults(run=_serve)

    commands.add_parser("mcp", help="run the MCP server for Claude").set_defaults(run=_mcp)
    for tool in ("digest", "inbox", "team"):
        commands.add_parser(tool, help=f"show {tool}").set_defaults(run=_tool, tool=tool)
    return parser


def _home(args: argparse.Namespace) -> Path:
    home: Path | None = args.home
    return home if home is not None else default_home()


def _passphrase(*, confirm: bool) -> bytes:
    from_env = os.environ.get(PASSPHRASE_ENV)
    if from_env:
        return from_env.encode()
    passphrase = getpass.getpass("Key passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != passphrase:
        raise CliError("passphrases do not match")
    if not passphrase:
        raise CliError("passphrase must not be empty")
    return passphrase.encode()


def _init(args: argparse.Namespace) -> int:
    home = _home(args)
    exchange = None if args.exchange is None else args.exchange.resolve()
    with Device.create_team(
        home, args.handle, _passphrase(confirm=True), team_name=args.team_name, exchange=exchange
    ) as device:
        sys.stdout.write(
            f"created team with {args.handle} as admin in {home}\n"
            f"invite  {device.invite.encode()}\n"
            "next: run `agentmesh serve`, then add `agentmesh mcp` to Claude\n"
        )
    return 0


def _serve(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    home = _home(args)
    with Device.open(home, _passphrase(confirm=False)) as device:
        sys.stderr.write(f"agentmesh serving {device.config.handle} from {home}\n")
        asyncio.run(_run_daemon(Daemon(device, default_endpoint(home), args.interval)))
    return 0


async def _run_daemon(daemon: Daemon) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await daemon.run(stop)
    except asyncio.CancelledError:
        stop.set()


def _mcp(args: argparse.Namespace) -> int:
    build_server(RpcClient(default_endpoint(_home(args)))).run("stdio")
    return 0


def _tool(args: argparse.Namespace) -> int:
    client = RpcClient(default_endpoint(_home(args)))
    sys.stdout.write(asyncio.run(client.call(args.tool)) + "\n")
    return 0
