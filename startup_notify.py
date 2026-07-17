#!/usr/bin/env python3
"""Run in the background by run_server.sh on every start of mc-server.service.

Tells a sleepd-triggered wake (WAKE_MARKER_FILE present, sleepd already sent
its own "starting up" notice) apart from a manual start or crash-restart
(marker absent - worth its own notice), then waits for RCON to come up and
announces that the server is ready to log into.
"""

import asyncio
import os
import time

import config
import notifier
import rcon


async def wait_until_ready():
    password = open(config.RCON_PASSWORD_FILE).read().strip()
    deadline = time.monotonic() + config.STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            await rcon.rcon_command(config.RCON_HOST, config.RCON_PORT, password, "list")
            return True
        except Exception:
            await asyncio.sleep(config.STARTUP_POLL_INTERVAL_SECONDS)
    return False


async def main():
    if os.path.exists(config.WAKE_MARKER_FILE):
        os.remove(config.WAKE_MARKER_FILE)
    else:
        notifier.notify("Server started manually")

    if await wait_until_ready():
        notifier.notify("Server is ready!")


if __name__ == "__main__":
    asyncio.run(main())
