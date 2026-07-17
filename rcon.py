"""Minimal asyncio client for the Source RCON protocol (used by Minecraft)."""

import asyncio
import struct

SERVERDATA_AUTH = 3
SERVERDATA_EXECCOMMAND = 2


class RconError(Exception):
    pass


async def rcon_command(host, port, password, command, timeout=5.0):
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        await _send_packet(writer, 1, SERVERDATA_AUTH, password)
        auth_id, _, _ = await asyncio.wait_for(_read_packet(reader), timeout)
        if auth_id == -1:
            raise RconError("RCON authentication failed (wrong password?)")

        await _send_packet(writer, 2, SERVERDATA_EXECCOMMAND, command)
        _, _, response = await asyncio.wait_for(_read_packet(reader), timeout)
        return response.decode("utf-8", errors="replace")
    finally:
        writer.close()
        await writer.wait_closed()


async def _send_packet(writer, request_id, packet_type, payload):
    body = struct.pack("<ii", request_id, packet_type) + payload.encode("utf-8") + b"\x00\x00"
    writer.write(struct.pack("<i", len(body)) + body)
    await writer.drain()


async def _read_packet(reader):
    length = struct.unpack("<i", await reader.readexactly(4))[0]
    body = await reader.readexactly(length)
    request_id, packet_type = struct.unpack("<ii", body[:8])
    payload = body[8:-2]
    return request_id, packet_type, payload
