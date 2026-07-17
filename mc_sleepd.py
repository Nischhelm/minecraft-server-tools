#!/usr/bin/env python3
"""Sleep/wake daemon for the Minecraft server.

States:
  SLEEPING - mc-server.service is inactive, we listen on the public port
             ourselves and answer status pings with a "sleeping" response.
             A real login attempt wakes the server.
  STARTING - systemctl start was triggered, we poll via RCON until ready.
  RUNNING  - Server is running. We poll player count (idle timeout) and
             uptime (forced 6h restart with warning) via RCON.
"""

import asyncio
import logging
import re
import time

import config
import mcproto
import notifier
import rcon

log = logging.getLogger("mc-sleepd")


async def systemctl(*args):
    cmd = ["systemctl"]
    if config.SYSTEMD_SCOPE:
        cmd.append(config.SYSTEMD_SCOPE)
    cmd.extend(args)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    # "is-active" deliberately returns a non-zero exit code when the service
    # is inactive - that's the normal resting state, not worth a warning.
    if proc.returncode != 0 and "is-active" not in args:
        log.warning("systemctl %s -> rc=%s stderr=%s", args, proc.returncode, stderr.decode().strip())
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


class SleepListener:
    """Listens on the public port while the real server is off."""

    def __init__(self, on_wake):
        self._on_wake = on_wake
        self._server = None

    @property
    def active(self):
        return self._server is not None

    async def start(self):
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_client, config.MC_HOST, config.MC_PORT)
        log.info("Sleep listener active on port %d", config.MC_PORT)

    async def stop(self):
        if self._server is None:
            return
        server = self._server
        self._server = None
        # Deliberately no `await server.wait_closed()`: stop() is called from
        # wake_server(), which itself runs as the handler of ONE connection to
        # this very server. wait_closed() waits for all active connections
        # including the one currently running - that would deadlock on itself
        # (Python 3.12). server.close() alone is enough to free the port
        # immediately for the real server.
        server.close()
        log.info("Sleep listener released (port %d)", config.MC_PORT)

    async def _handle_client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        try:
            await self._process(reader, writer, peer)
        except (asyncio.IncompleteReadError, ConnectionResetError, mcproto.ProtocolError, OSError) as exc:
            log.debug("Connection from %s ended: %s", peer, exc)
        finally:
            writer.close()

    async def _process(self, reader, writer, peer):
        handshake_packet = await mcproto.read_packet(reader)
        handshake = mcproto.parse_handshake(handshake_packet)

        if handshake.next_state == 1:
            await self._handle_status(reader, writer)
        elif handshake.next_state == 2:
            login_packet = await mcproto.read_packet(reader)
            name = mcproto.parse_login_start_name(login_packet)
            if mcproto.is_forge_client(handshake.server_address):
                log.info("Login attempt from %s (player: %s) - waking server", peer, name)
                writer.write(mcproto.build_disconnect_packet(config.WAKE_KICK_MESSAGE))
                await writer.drain()
                await self._on_wake(peer, name)
            else:
                log.info("Login attempt from %s (player: %s) - vanilla client, not waking", peer, name)
                writer.write(mcproto.build_disconnect_packet(config.VANILLA_KICK_MESSAGE))
                await writer.drain()

    async def _handle_status(self, reader, writer):
        await mcproto.read_packet(reader)  # status request, content irrelevant
        writer.write(
            mcproto.build_status_response_packet(
                config.SLEEP_MOTD, config.MC_PROTOCOL_VERSION, config.MC_VERSION_NAME, config.MAX_PLAYERS
            )
        )
        await writer.drain()
        try:
            ping_packet_len = await mcproto.read_varint_async(reader)
            ping_payload = await reader.readexactly(ping_packet_len)
            writer.write(mcproto.write_varint(len(ping_payload)) + ping_payload)
            await writer.drain()
        except (asyncio.IncompleteReadError, mcproto.ProtocolError):
            pass


class SleepDaemon:
    def __init__(self):
        self.listener = SleepListener(self.wake_server)
        self.state = "SLEEPING"
        self.idle_since = None
        self.server_start_time = None
        self.warned_offsets_sent = set()
        self._wake_lock = asyncio.Lock()

    async def run(self):
        if await self._server_is_active():
            log.info("%s is already running when mc-sleepd starts", config.SYSTEMD_UNIT)
            self.state = "RUNNING"
            self.server_start_time = time.monotonic()
        else:
            self.state = "SLEEPING"
            await self.listener.start()

        while True:
            if self.state == "SLEEPING":
                await asyncio.sleep(5)
            elif self.state == "STARTING":
                await self._wait_for_startup()
            elif self.state == "RUNNING":
                await self._poll_running()
                if self.state == "RUNNING":
                    await asyncio.sleep(config.POLL_INTERVAL_SECONDS)

    async def wake_server(self, peer, name):
        async with self._wake_lock:
            if self.state != "SLEEPING":
                return
            self.state = "STARTING"
            await self.listener.stop()
            log.info("Starting %s", config.SYSTEMD_UNIT)
            await asyncio.to_thread(notifier.notify, f"Server starting up ({name} connecting)")
            open(config.WAKE_MARKER_FILE, "w").close()
            await systemctl("start", config.SYSTEMD_UNIT)

    async def _wait_for_startup(self):
        started_at = time.monotonic()
        while time.monotonic() - started_at < config.STARTUP_TIMEOUT_SECONDS:
            if await self._rcon_reachable():
                log.info("Server is ready (RCON responding)")
                self.state = "RUNNING"
                self.server_start_time = time.monotonic()
                self.idle_since = None
                self.warned_offsets_sent = set()
                return
            if not await self._server_is_active():
                log.warning("%s stopped during startup, going back to sleep", config.SYSTEMD_UNIT)
                self.state = "SLEEPING"
                await self.listener.start()
                return
            await asyncio.sleep(config.STARTUP_POLL_INTERVAL_SECONDS)

        if await self._server_is_active():
            log.warning(
                "Startup timeout reached but %s is still active, giving it more time",
                config.SYSTEMD_UNIT,
            )
            return

        log.error("Timed out waiting for server startup, going back to sleep")
        self.state = "SLEEPING"
        await self.listener.start()

    async def _poll_running(self):
        if not await self._server_is_active():
            log.info("%s is no longer active, going to sleep", config.SYSTEMD_UNIT)
            self.state = "SLEEPING"
            self.server_start_time = None
            self.idle_since = None
            self.warned_offsets_sent = set()
            await self.listener.start()
            return

        player_count = await self._player_count()
        now = time.monotonic()

        if player_count is not None:
            if player_count > 0:
                self.idle_since = None
            else:
                if self.idle_since is None:
                    self.idle_since = now
                elif now - self.idle_since >= config.IDLE_TIMEOUT_SECONDS:
                    minutes = config.IDLE_TIMEOUT_SECONDS // 60
                    log.info("No players online for %d minutes, stopping server", minutes)
                    await asyncio.to_thread(notifier.notify, f"Server going to sleep (no players for {minutes} min)")
                    await systemctl("stop", config.SYSTEMD_UNIT)
                    return

        if self.server_start_time is None:
            return
        uptime = now - self.server_start_time
        remaining = config.RESTART_INTERVAL_SECONDS - uptime

        for offset in config.RESTART_WARNING_OFFSETS_SECONDS:
            if remaining <= offset and offset not in self.warned_offsets_sent:
                minutes = max(1, round(offset / 60))
                await self._try_rcon(f"say §c[Restart] Scheduled server restart in {minutes} minute(s)!")
                self.warned_offsets_sent.add(offset)

        if remaining <= 0:
            log.info("Triggering scheduled 6h restart")
            await asyncio.to_thread(notifier.notify, "Server restarting (scheduled 6h restart)")
            await systemctl("restart", config.SYSTEMD_UNIT)
            self.server_start_time = time.monotonic()
            self.warned_offsets_sent = set()

    def _read_rcon_password(self):
        with open(config.RCON_PASSWORD_FILE) as f:
            return f.read().strip()

    async def _rcon(self, command):
        password = self._read_rcon_password()
        return await rcon.rcon_command(config.RCON_HOST, config.RCON_PORT, password, command)

    async def _try_rcon(self, command):
        try:
            return await self._rcon(command)
        except Exception as exc:
            log.debug("RCON command '%s' failed: %s", command, exc)
            return None

    async def _rcon_reachable(self):
        try:
            await self._rcon("list")
            return True
        except Exception:
            return False

    async def _player_count(self):
        response = await self._try_rcon("list")
        if response is None:
            return None
        match = re.search(r"There are (\d+)/\d+ players online", response)
        return int(match.group(1)) if match else None

    async def _server_is_active(self):
        _, stdout, _ = await systemctl("is-active", config.SYSTEMD_UNIT)
        return stdout == "active"


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await SleepDaemon().run()


if __name__ == "__main__":
    asyncio.run(main())
