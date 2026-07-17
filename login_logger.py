#!/usr/bin/env python3
"""Tails the Minecraft server's and sleep daemon's console output (via
journalctl) and records both real logins and login attempts that never
completed to logins.csv - timestamp, player, ip, status.

status is "login" for a completed join, "attempt" for one that didn't, "bot"
for a login attempt while sleeping from a client that never identified as
Forge-modded (see mcproto.is_forge_client) and so was kicked without waking
the server, "leave" for a player disconnecting:
  - a wake attempt while the server was sleeping that was never followed by
    a real join (e.g. a server-list scanner), or
  - a connection to the already-running server that never finished the FML
    handshake (e.g. mismatched mod list).

If the same player name goes on to log in for real within the grace period,
the earlier attempt is dropped and only a single "login" row is written -
attempts are only flushed once their grace period expires without a login.

In-game chat is recorded separately to chat.csv - timestamp, player, message
- and also mirrored to Discord (unlike logins.csv, chat volume doesn't get a
grace-period/dedup treatment - each message is its own row).

Dimension changes are recorded to dimensions.csv - timestamp, player, from,
to - and mirrored to Discord. Emitted by the MinecraftServerTool mod
(sleepd/mod/) as a "[dimchange] player=... from=... to=..." console line on
PlayerChangedDimensionEvent.

Runs independently of mc_sleepd.py's state machine, watching both
mc-server.service (for the FML "UUID of player" attempt marker, the
"logged in with entity id" confirmation, chat, and "left the game") and
mc-sleepd.service (for wake attempts, which carry an IP the running server
never sees since the real FML handshake hasn't started yet).
"""

import csv
import datetime
import os
import re
import selectors
import subprocess
import time

import config
import notifier

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "logins.csv")
CHAT_LOG_FILE = os.path.join(BASE_DIR, "chat.csv")
DIMENSION_LOG_FILE = os.path.join(BASE_DIR, "dimensions.csv")
KNOWN_BOTS_FILE = os.path.join(BASE_DIR, "known_bots.txt")
SLEEPD_UNIT = "mc-sleepd.service"

SWEEP_INTERVAL_SECONDS = 5
# A wake attempt can be followed by a real join many minutes later (mod boot
# time + the player noticing the kick message and reconnecting).
WAKE_ATTEMPT_GRACE_SECONDS = config.STARTUP_TIMEOUT_SECONDS + 5 * 60
# A running-server FML handshake resolves within seconds either way.
RUNNING_ATTEMPT_GRACE_SECONDS = 60

WAKE_ATTEMPT_RE = re.compile(r"Login attempt from \('(?P<ip>[^']+)', \d+\) \(player: (?P<name>\S+)\) - waking server")
BOT_ATTEMPT_RE = re.compile(
    r"Login attempt from \('(?P<ip>[^']+)', \d+\) \(player: (?P<name>\S+)\) - vanilla client, not waking"
)
UUID_RE = re.compile(r"UUID of player (?P<name>\S+) is")
LOGIN_RE = re.compile(r"(?P<name>\S+)\[/(?P<ip>[0-9a-fA-F:.]+):(?P<port>\d+)\] logged in with entity id")
# Anchored to the standard log4j prefix (timestamp, thread/level, optional
# named-logger marker) with the player name immediately after it - real chat
# lines always look exactly like this. A looser "<x> rest of line" pattern
# also matched unrelated startup noise that happens to contain <angle
# brackets> (the Mixin subsystem's banner, ASM/bytecode debug dumps like
# "BlockPistonStructureHelper.<init> (Lnet/...)V"), which isn't preceded by
# a log prefix or has other text before the bracketed token.
CHAT_RE = re.compile(
    r"^\[\d\d:\d\d:\d\d\] \[[^\]]+\](?: \[[^\]]+\])?: <(?P<name>[A-Za-z0-9_]{3,16})> (?P<message>.+)$"
)
LEAVE_RE = re.compile(r"(?P<name>\S+) left the game")
DIMCHANGE_RE = re.compile(r"\[dimchange\] player=(?P<name>\S+) from=(?P<from>.+?) to=(?P<to>.+)")

# name -> {"since": monotonic time, "ip": str or None, "grace": seconds}
pending = {}

# Names ever seen with status "bot" (vanilla-client server-list scanners).
# Persisted to KNOWN_BOTS_FILE so each one is only ever announced on
# Discord once, even across restarts - every attempt still lands in
# logins.csv regardless.
known_bots = set()


def ensure_header():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["timestamp", "player", "ip", "status"])
    if not os.path.exists(CHAT_LOG_FILE):
        with open(CHAT_LOG_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["timestamp", "player", "message"])
    if not os.path.exists(DIMENSION_LOG_FILE):
        with open(DIMENSION_LOG_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["timestamp", "player", "from", "to"])


def load_known_bots():
    known_bots.clear()
    try:
        with open(KNOWN_BOTS_FILE) as f:
            known_bots.update(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        pass


def remember_bot(name):
    known_bots.add(name)
    with open(KNOWN_BOTS_FILE, "a") as f:
        f.write(name + "\n")


def write_row(name, ip, status):
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow([timestamp, name, ip or "unknown", status])
    print(f"{timestamp} {status}: {name} from {ip or 'unknown'}", flush=True)
    if status == "login":
        notifier.notify(f"{name} logged in")
    elif status == "bot":
        if name not in known_bots:
            remember_bot(name)
            notifier.notify(f"{name} tried to join without the modpack (not woken, likely a scanner)")
    elif status == "leave":
        notifier.notify(f"{name} left the game")
    else:
        notifier.notify(f"{name} tried to join but didn't connect")


def write_chat_row(name, message):
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(CHAT_LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow([timestamp, name, message])
    print(f"{timestamp} chat: <{name}> {message}", flush=True)
    notifier.notify(f"<{name}> {message}")


def write_dimension_row(name, from_dim, to_dim):
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(DIMENSION_LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow([timestamp, name, from_dim, to_dim])
    print(f"{timestamp} dimchange: {name} {from_dim} -> {to_dim}", flush=True)
    notifier.notify(f"{name} moved from {from_dim} to {to_dim}")


def handle_line(line):
    match = LOGIN_RE.search(line)
    if match:
        pending.pop(match.group("name"), None)
        write_row(match.group("name"), match.group("ip"), "login")
        return

    match = WAKE_ATTEMPT_RE.search(line)
    if match:
        pending[match.group("name")] = {
            "since": time.monotonic(),
            "ip": match.group("ip"),
            "grace": WAKE_ATTEMPT_GRACE_SECONDS,
        }
        return

    match = BOT_ATTEMPT_RE.search(line)
    if match:
        # Server was never woken for this one, nothing to wait for - log it
        # immediately instead of going through the pending/grace dance.
        write_row(match.group("name"), match.group("ip"), "bot")
        return

    match = UUID_RE.search(line)
    if match:
        name = match.group("name")
        if name not in pending:
            pending[name] = {"since": time.monotonic(), "ip": None, "grace": RUNNING_ATTEMPT_GRACE_SECONDS}
        return

    match = DIMCHANGE_RE.search(line)
    if match:
        write_dimension_row(match.group("name"), match.group("from"), match.group("to"))
        return

    # Checked before LEAVE_RE: a chat message that literally says "left the
    # game" (e.g. "<Alice> left the game") must not be mistaken for a real
    # leave - the "<name>" bracket syntax only ever appears in chat lines.
    match = CHAT_RE.search(line)
    if match:
        write_chat_row(match.group("name"), match.group("message"))
        return

    match = LEAVE_RE.search(line)
    if match:
        write_row(match.group("name"), None, "leave")


def sweep_pending():
    now = time.monotonic()
    expired = [name for name, entry in pending.items() if now - entry["since"] >= entry["grace"]]
    for name in expired:
        entry = pending.pop(name)
        write_row(name, entry["ip"], "attempt")


def main():
    ensure_header()
    load_known_bots()
    cmd = ["journalctl"]
    if config.SYSTEMD_SCOPE:
        cmd.append(config.SYSTEMD_SCOPE)
    cmd.extend(["-u", config.SYSTEMD_UNIT, "-u", SLEEPD_UNIT, "-f", "-n", "0", "-o", "cat"])

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, bufsize=1)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)

    while True:
        events = sel.select(timeout=SWEEP_INTERVAL_SECONDS)
        if events:
            line = proc.stdout.readline()
            if not line:
                break
            handle_line(line)
        sweep_pending()


if __name__ == "__main__":
    main()
