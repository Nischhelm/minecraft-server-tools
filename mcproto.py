"""Minimal subset of the Minecraft network protocol: just enough to answer
a server list ping (status) and detect a real login attempt.
Not a full server emulation."""

import io
import struct


class ProtocolError(Exception):
    pass


async def read_varint_async(reader):
    value = 0
    for i in range(5):
        byte = (await reader.readexactly(1))[0]
        value |= (byte & 0x7F) << (7 * i)
        if not (byte & 0x80):
            return value
    raise ProtocolError("VarInt too big")


async def read_packet(reader):
    length = await read_varint_async(reader)
    if length < 0 or length > 1 << 20:
        raise ProtocolError("Implausible packet length")
    return await reader.readexactly(length)


def write_varint(value):
    out = bytearray()
    value &= 0xFFFFFFFF
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def write_string(s):
    data = s.encode("utf-8")
    return write_varint(len(data)) + data


def _sync_read_varint(buf):
    value = 0
    for i in range(5):
        chunk = buf.read(1)
        if not chunk:
            raise ProtocolError("Unexpected end while reading a VarInt")
        byte = chunk[0]
        value |= (byte & 0x7F) << (7 * i)
        if not (byte & 0x80):
            return value
    raise ProtocolError("VarInt too big")


def _sync_read_string(buf):
    length = _sync_read_varint(buf)
    data = buf.read(length)
    return data.decode("utf-8", errors="replace")


class Handshake:
    __slots__ = ("protocol_version", "server_address", "server_port", "next_state")

    def __init__(self, protocol_version, server_address, server_port, next_state):
        self.protocol_version = protocol_version
        self.server_address = server_address
        self.server_port = server_port
        self.next_state = next_state


def parse_handshake(packet_bytes):
    buf = io.BytesIO(packet_bytes)
    packet_id = _sync_read_varint(buf)
    if packet_id != 0x00:
        raise ProtocolError(f"Not a handshake packet (id={packet_id})")
    protocol_version = _sync_read_varint(buf)
    server_address = _sync_read_string(buf)
    (server_port,) = struct.unpack(">H", buf.read(2))
    next_state = _sync_read_varint(buf)
    return Handshake(protocol_version, server_address, server_port, next_state)


def parse_login_start_name(packet_bytes):
    try:
        buf = io.BytesIO(packet_bytes)
        _sync_read_varint(buf)  # packet id
        return _sync_read_string(buf)
    except ProtocolError:
        return "?"


# Pre-1.13 Forge clients (this server is 1.12.2) always append this marker to
# the handshake's server_address, on every connection - status ping or login,
# not just after a prior probe. Generic server-list scanners never send it
# since it's not part of the base protocol. See wiki.vg/Minecraft_Forge_Handshake.
FML_MARKER = "\x00FML\x00"


def is_forge_client(server_address):
    return FML_MARKER in server_address


def build_status_response_packet(description_text, protocol_version, version_name, max_players):
    import json

    payload_json = json.dumps(
        {
            "version": {"name": version_name, "protocol": protocol_version},
            "players": {"max": max_players, "online": 0, "sample": []},
            "description": {"text": description_text},
        }
    )
    body = write_varint(0x00) + write_string(payload_json)
    return write_varint(len(body)) + body


def build_disconnect_packet(reason_text):
    import json

    body = write_varint(0x00) + write_string(json.dumps({"text": reason_text}))
    return write_varint(len(body)) + body
