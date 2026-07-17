#!/usr/bin/env python3
"""Tiny RCON CLI so you don't need mcrcon or any other external tool.

Usage:
    python3 rcon_cli.py list
    python3 rcon_cli.py say Hello everyone!
    python3 rcon_cli.py "give Nischhelm minecraft:diamond 5"
"""

import asyncio
import sys

import config
import rcon


async def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <command...>")
        sys.exit(1)

    command = " ".join(sys.argv[1:])
    with open(config.RCON_PASSWORD_FILE) as f:
        password = f.read().strip()

    try:
        response = await rcon.rcon_command(config.RCON_HOST, config.RCON_PORT, password, command)
    except Exception as exc:
        print(f"RCON error: {exc}", file=sys.stderr)
        print("(Is mc-server.service running? RCON only comes up once the world is loaded.)", file=sys.stderr)
        sys.exit(1)

    print(response if response else "(empty response)")


if __name__ == "__main__":
    asyncio.run(main())
