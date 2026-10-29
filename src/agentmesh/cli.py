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
from agentmesh.identity.backends import Backend
from agentmesh.log.names import parse_address
from agentmesh.mcp.server import build_server
from agentmesh.relay.server import Relay, RelayError
from agentmesh.service.config import ConfigError, DeviceConfig, default_home
from agentmesh.service.daemon import DEFAULT_SYNC_INTERVAL, Daemon
from agentmesh.service.device import Device
from agentmesh.service.invite import Invite, InviteError
from agentmesh.service.peer_sync import DEFAULT_PEER_INTERVAL, PeerSync
from agentmesh.service.rpc import RpcClient, RpcError, default_endpoint
from agentmesh.transport.lan import ZeroconfDiscovery
from agentmesh.transport.relays import RelayDirectory

PASSPHRASE_ENV = "AGENTMESH_PASSPHRASE"  # noqa: S105 - env var name, not a secret
DEFAULT_RELAY_PORT = 7447
AUTO_KEY = "auto"
KEY_CHOICES = [AUTO_KEY, *(backend.value for backend in Backend)]
_KEY_HELP = "where the signing key lives; auto uses a hardware key if this machine has one"


class CliError(Exception):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.run(args))
    except (CliError, ConfigError, IdentityError, RelayError, RpcError) as exc:
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
    init.add_argument("--key", choices=KEY_CHOICES, default=AUTO_KEY, help=_KEY_HELP)
    init.set_defaults(run=_init)

    invite = commands.add_parser("invite", help="print this team's invite code")
    invite.add_argument("--relay", help="host:port of a relay to put in the code")
    invite.set_defaults(run=_invite)

    join = commands.add_parser("join", help="join a team from an invite code")
    join.add_argument("code", help="invite code from a teammate")
    join.add_argument("--handle", required=True, help="your short name, e.g. tobi")
    join.add_argument("--exchange", type=Path, help="shared folder for swapping entries")
    join.add_argument("--key", choices=KEY_CHOICES, default=AUTO_KEY, help=_KEY_HELP)
    join.set_defaults(run=_join)

    _add_roster_commands(commands)
    _add_relay_commands(commands)

    serve = commands.add_parser("serve", help="run the background service")
    serve.add_argument("--interval", type=float, default=DEFAULT_SYNC_INTERVAL)
    serve.add_argument("--no-lan", action="store_true", help="do not sync over the local network")
    serve.add_argument("--lan-interval", type=float, default=DEFAULT_PEER_INTERVAL)
    serve.set_defaults(run=_serve)

    commands.add_parser("mcp", help="run the MCP server for Claude").set_defaults(run=_mcp)
    for tool in ("digest", "inbox", "team"):
        commands.add_parser(tool, help=f"show {tool}").set_defaults(run=_tool, tool=tool)
    return parser


def _add_roster_commands(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    roster = commands.add_parser("roster", help="show or change the team roster")
    roster.set_defaults(run=_roster_show)
    roster_commands = roster.add_subparsers(metavar="action")
    add = roster_commands.add_parser("add", help="add a member or a device (admins only)")
    add.add_argument("handle")
    add.add_argument("key", help="device key printed by `agentmesh join`")
    add.add_argument("--role", action="append", default=[], help="a role such as @mobile")
    add.add_argument("--channel", action="append", default=[], help="a channel such as #mobile")
    add.add_argument("--admin", action="store_true", help="allow this member to change the roster")
    add.add_argument("--label", default="", help="device name shown in the roster")
    add.add_argument(
        "--hardware", action="store_true", help="the key is in hardware, as `join` reported"
    )
    add.set_defaults(run=_roster_add)
    remove = roster_commands.add_parser("remove", help="remove a member (admins only)")
    remove.add_argument("handle")
    remove.set_defaults(run=_roster_remove)
    relay_add = roster_commands.add_parser("relay-add", help="add a relay (admins only)")
    relay_add.add_argument("address", help="host:port the relay listens on")
    relay_add.add_argument("key", help="relay key printed by `agentmesh relay init`")
    relay_add.set_defaults(run=_roster_relay_add)
    relay_remove = roster_commands.add_parser("relay-remove", help="remove a relay (admins only)")
    relay_remove.add_argument("address")
    relay_remove.set_defaults(run=_roster_relay_remove)


def _add_relay_commands(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    relay = commands.add_parser("relay", help="set up or run a relay for a team")
    relay_commands = relay.add_subparsers(required=True, metavar="action")
    relay_init = relay_commands.add_parser("init", help="create a relay from an invite code")
    relay_init.add_argument("--invite", required=True, help="invite code from a team admin")
    relay_init.set_defaults(run=_relay_init)
    relay_serve = relay_commands.add_parser("serve", help="run the relay")
    relay_serve.add_argument("--listen", default=f"0.0.0.0:{DEFAULT_RELAY_PORT}", help="host:port")
    relay_serve.set_defaults(run=_relay_serve)


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
        home,
        args.handle,
        _passphrase(confirm=True),
        team_name=args.team_name,
        exchange=exchange,
        key_backend=_key_backend(args),
    ) as device:
        sys.stdout.write(
            f"created team with {args.handle} as admin in {home}\n"
            f"{_describe_key(device.config.key_backend)}\n"
            f"invite  {device.invite.encode()}\n"
            "next: run `agentmesh serve`, then add `agentmesh mcp` to Claude\n"
        )
    return 0


def _invite(args: argparse.Namespace) -> int:
    home = _home(args)
    config = DeviceConfig.load(home)
    try:
        current = Invite.decode(asyncio.run(RpcClient(default_endpoint(home)).call("invite")))
    except RpcError:
        current = Invite(config.team, config.genesis, config.relay)
    try:
        code = Invite(current.team, current.genesis, args.relay or current.relay).encode()
    except InviteError as exc:
        raise CliError(str(exc)) from exc
    sys.stdout.write(f"{code}\nshare this with a teammate; they run `agentmesh join {code}`\n")
    return 0


def _join(args: argparse.Namespace) -> int:
    try:
        invite = Invite.decode(args.code)
    except InviteError as exc:
        raise CliError(str(exc)) from exc
    home = _home(args)
    exchange = None if args.exchange is None else args.exchange.resolve()
    with Device.join(
        home,
        invite,
        args.handle,
        _passphrase(confirm=True),
        exchange=exchange,
        key_backend=_key_backend(args),
    ) as device:
        hardware = " --hardware" if device.config.key_backend.hardware else ""
        sys.stdout.write(
            f"joined as {args.handle} in {home}\n"
            f"{_describe_key(device.config.key_backend)}\n"
            "ask an admin to run:\n"
            f"  agentmesh roster add {args.handle} {device.author.hex()}{hardware}\n"
        )
    return 0


def _key_backend(args: argparse.Namespace) -> Backend | None:
    return None if args.key == AUTO_KEY else Backend(args.key)


def _describe_key(backend: Backend) -> str:
    if backend.hardware:
        return f"signing key  {backend} (hardware, cannot be copied off this machine)"
    return f"signing key  {backend} (passphrase-protected file)"


def _roster_show(args: argparse.Namespace) -> int:
    return _call(args, "roster.show", {})


def _roster_add(args: argparse.Namespace) -> int:
    params: dict[str, object] = {
        "handle": args.handle,
        "key": args.key,
        "roles": args.role,
        "channels": args.channel,
        "admin": args.admin,
        "label": args.label,
        "hardware": args.hardware,
    }
    return _call(args, "roster.add", params)


def _roster_remove(args: argparse.Namespace) -> int:
    return _call(args, "roster.remove", {"handle": args.handle})


def _roster_relay_add(args: argparse.Namespace) -> int:
    return _call(args, "roster.relay_add", {"address": args.address, "key": args.key})


def _roster_relay_remove(args: argparse.Namespace) -> int:
    return _call(args, "roster.relay_remove", {"address": args.address})


def _relay_home(args: argparse.Namespace) -> Path:
    home: Path | None = args.home
    return home if home is not None else Path.home() / ".agentmesh-relay"


def _relay_init(args: argparse.Namespace) -> int:
    try:
        invite = Invite.decode(args.invite)
    except InviteError as exc:
        raise CliError(str(exc)) from exc
    home = _relay_home(args)
    with Relay.create(home, invite.team, invite.genesis, _passphrase(confirm=True)) as relay:
        sys.stdout.write(
            f"created relay in {home}\n"
            "run it with `agentmesh relay serve`, then ask an admin to run:\n"
            f"  agentmesh roster relay-add <public-host>:{DEFAULT_RELAY_PORT} {relay.key.hex()}\n"
        )
    return 0


def _relay_serve(args: argparse.Namespace) -> int:
    parsed = parse_address(args.listen)
    if parsed is None:
        raise CliError(f"--listen must be host:port, got {args.listen!r}")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    home = _relay_home(args)
    with Relay.open(home, _passphrase(confirm=False)) as relay:
        sys.stderr.write(f"agentmesh relay {relay.key.hex()} listening on {args.listen}\n")
        asyncio.run(_run_relay(relay, *parsed))
    return 0


async def _run_relay(relay: Relay, host: str, port: int) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await relay.serve(host, port, stop)
    except asyncio.CancelledError:
        stop.set()


def _call(args: argparse.Namespace, method: str, params: dict[str, object]) -> int:
    client = RpcClient(default_endpoint(_home(args)))
    sys.stdout.write(asyncio.run(client.call(method, params)) + "\n")
    return 0


def _serve(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    home = _home(args)
    with Device.open(home, _passphrase(confirm=False)) as device:
        sys.stderr.write(f"agentmesh serving {device.config.handle} from {home}\n")
        peers = [_relay_sync(device, args.lan_interval)]
        if not args.no_lan:
            peers.append(_lan_sync(device, args.lan_interval))
        asyncio.run(_run_daemon(Daemon(device, default_endpoint(home), args.interval, peers)))
    return 0


def _relay_sync(device: Device, interval: float) -> PeerSync:
    directory = RelayDirectory(device.relay_addresses)
    return PeerSync(device.sync_node, directory, interval=interval, listen=False)


def _lan_sync(device: Device, interval: float) -> PeerSync:
    discovery = ZeroconfDiscovery(device.config.team, device.author)
    return PeerSync(device.sync_node, discovery, interval=interval)


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
    return _call(args, args.tool, {})
