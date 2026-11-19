"""Launch vdra in an isolated OpenViking server process."""

import argparse
import json
import os
import sys
from pathlib import Path


def load_config(path):
    with path.open(encoding="utf-8-sig") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("OpenViking configuration must be a JSON object")
    server = config.get("server")
    if not isinstance(server, dict) or server.get("host") != "127.0.0.1":
        raise ValueError("set server.host to 127.0.0.1 in the OpenViking configuration")
    storage = config.get("storage")
    workspace = storage.get("workspace", "") if isinstance(storage, dict) else ""
    if not isinstance(workspace, str) or not Path(workspace).is_absolute():
        raise ValueError("set storage.workspace to an absolute path")
    return config


def serve(config_path):
    from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV

    os.environ[OPENVIKING_CONFIG_ENV] = str(config_path)

    from .memory_policy import install as install_retention
    from .privacy_loop_compat import install as install_privacy_compat
    from .work_memory import install as install_work_memory
    from openviking_cli.server_bootstrap import main as start_openviking

    install_privacy_compat()
    install_retention()
    install_work_memory()
    sys.argv = ["openviking-server", "--config", str(config_path), "--workers", "1"]
    start_openviking()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vdra", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser(
        "serve", help="start a loopback OpenViking server with vdra"
    )
    default_home = Path(os.environ.get("VDRA_HOME", str(Path.home() / ".vdra")))
    server.add_argument("--home", type=Path, default=default_home)
    server.add_argument(
        "--config", type=Path, required=True, help="path to an OpenViking ov.conf file"
    )
    memory = commands.add_parser(
        "memory", help="manage vdra work memory and project bindings"
    )
    memory.add_argument("--home", type=Path, default=default_home)
    args, tail = parser.parse_known_args(argv)
    os.environ["VDRA_HOME"] = str(args.home.expanduser().resolve())

    if args.command == "memory":
        from .vdra_state import cli

        if not tail:
            tail = ["--help"]
        cli(tail)
        return

    if tail:
        server.error("unrecognized arguments: " + " ".join(tail))
    config_path = args.config.expanduser().resolve()
    try:
        load_config(config_path)
    except (OSError, ValueError) as error:
        server.error(str(error))
    serve(config_path)


if __name__ == "__main__":
    main()
