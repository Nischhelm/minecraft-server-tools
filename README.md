# minecraft-server-tools

`sleepd` - Sleep/Wake System for the RLCraft Server. Runs the Minecraft
server (`mc-server.service`) on demand instead of 24/7: sleeping when
nobody's around, waking on a real join attempt, sleeping again after 20
minutes with no players, and force-restarting every 6 hours with an in-game
warning.

Players connect without needing to specify a port - the server runs on
Minecraft's default port 25565 (`server-port` in `../server.properties`,
`MC_PORT` in `config.py`).

Three systemd **user** units are involved:

- **`mc-sleepd.service`** - the daemon (`mc_sleepd.py`) that watches everything.
  Meant to run permanently (starts at boot via `loginctl enable-linger`).
- **`mc-server.service`** - the actual Java/Forge server. Started and stopped
  automatically by `mc-sleepd`; you normally never touch this by hand. Has
  `CPUWeight=300`/`IOWeight=300` (vs. the default 100) so it's prioritized
  over other services sharing this host.
- **`mc-loginlog.service`** - independent login/chat/bot-attempt logger, see below.

All commands below use `systemctl --user` (not plain `systemctl`) because
these are user-level units.

## Managing the services

Standard `systemctl --user` subcommands (`status`, `start`, `stop`,
`restart`, `enable`/`disable` for autostart at boot) and
`journalctl --user -u <unit> -f` (live logs) all work against any of the
three units above.

A few things worth knowing:

- `stop`/`start`/`restart` on `mc-server` are safe to run directly any time -
  `mc-sleepd` just reacts to whatever state it finds (goes back to sleep once
  the server stops, does nothing if you start it directly).
- Stopping/disabling `mc-sleepd` pauses the whole sleep/wake system (the
  server won't auto-wake on join anymore, but a currently-running server
  keeps running); `loginctl disable-linger $USER` additionally undoes the
  boot-persistence entirely.

## Talking to the running server (RCON)

No need to install `mcrcon` or anything else - there's a tiny built-in CLI:

```bash
cd ~/minecraft/sleepd
python3 rcon_cli.py list
python3 rcon_cli.py say Hello everyone!
python3 rcon_cli.py "give PlayerName minecraft:diamond 5"
```

It reads the password from `rcon_password.txt` automatically. Only works
while `mc-server` is actually running (RCON comes up once the world has
loaded, near the end of boot).

## Login, chat & bot-attempt logging

`mc-loginlog.service` (`login_logger.py`) tails both `mc-server` and
`mc-sleepd`'s console output (via journalctl), independent of `mc-sleepd`'s
own state, and writes two CSVs:

- **`logins.csv`** - `timestamp,player,ip,status`, one row per event. `status` is:
  - `login` - a completed join
  - `attempt` - a login that never completed (e.g. mismatched mod list)
  - `bot` - a login attempt while sleeping from a client that never
    identified as Forge-modded, i.e. a server-list scanner - kicked without
    waking the server
  - `leave` - a player disconnecting
- **`chat.csv`** - `timestamp,player,message`, one row per in-game chat message.

```bash
cat ~/minecraft/sleepd/logins.csv
cat ~/minecraft/sleepd/chat.csv
journalctl --user -u mc-loginlog -f   # watch new events live
```

Both files only ever append; nothing rotates or trims them, so clean them up
yourself if they grow large.

## Discord notifications

`notifier.py` mirrors every login-logger event (login, leave, attempt, chat
message) to a Discord webhook - fully optional, silently does nothing if
`discord_webhook_url.txt` doesn't exist. To enable it, put the webhook URL as
the only line in `discord_webhook_url.txt` (`chmod 600`, gitignored - never
commit it).

`bot` events (server-list scanners) are special-cased: each distinct player
name is only ever announced on Discord once, tracked in `known_bots.txt`
(auto-created, one name per line, gitignored). Every attempt still lands in
`logins.csv` regardless - only the repeat Discord spam is suppressed.

## Region maps

`region_map.py` renders a PNG per dimension (overworld, nether, end, lost
cities) showing which regions (32x32-chunk `.mca` files) have been
generated, colored by how recently each region file was last modified - a
rough "what's been explored, and how recently" map, not a biome/terrain map.
Spawn (block 0,0) is marked with a red dot.

Run manually:

```bash
python3 region_map.py
```

Output goes to `maps/` (gitignored - regenerated from world data, not
source). Sending these to Discord periodically is a planned future step, not
done yet.

## What each file does

### Scripts

| File | Purpose |
|---|---|
| `mc_sleepd.py` | The daemon itself: SLEEPING → STARTING → RUNNING state machine, idle-timeout and 6h-restart logic |
| `mcproto.py` | Minimal Minecraft handshake/status/login-packet parsing (just enough to fake a "sleeping" server and detect real join attempts) |
| `rcon.py` | Minimal Source RCON client, used internally by `mc_sleepd.py` |
| `rcon_cli.py` | Standalone command-line tool for sending yourself RCON commands (see above) |
| `config.py` | All the tunable settings: ports, timeouts (20 min idle / 6h restart), messages, RCON/Discord paths |
| `run_server.sh` | The actual `java ...` invocation used as `mc-server.service`'s `ExecStart` |
| `login_logger.py` | Tails the server console and appends every login/attempt/bot-probe/leave to `logins.csv` and every chat message to `chat.csv` |
| `notifier.py` | Posts login-logger events to a Discord webhook, if configured |
| `region_map.py` | Renders per-dimension "recently touched" region maps (see above) |
| `systemd/mc-server.service`, `systemd/mc-sleepd.service`, `systemd/mc-loginlog.service` | The canonical unit files, symlinked into `~/.config/systemd/user/` |

### Auto-generated files

| File | Purpose |
|---|---|
| `logins.csv`, `chat.csv` | The login/chat history itself (see above); grows locally |
| `known_bots.txt` | Player names already announced once as scanner `bot` attempts, so Discord isn't spammed on repeats |
| `maps/` | Output of `region_map.py`; regenerate anytime |

### Secrets

| File | Purpose |
|---|---|
| `rcon_password.txt` | Auto-generated RCON password (also written into `../server.properties`); `chmod 600` |
| `discord_webhook_url.txt` | Discord webhook URL for notifications (see above); `chmod 600` |
