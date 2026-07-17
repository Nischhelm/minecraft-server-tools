# minecraft-server-tools

`sleepd` - Sleep/Wake System for the RLCraft Server. Runs the Minecraft
server (`mc-server.service`) on demand instead of 24/7: sleeping when
nobody's around, waking on a real join attempt, sleeping again after 20
minutes with no players, and force-restarting every 6 hours with an in-game
warning.

Players connect without needing to specify a port - the server runs on
Minecraft's default port 25565 (`server-port` in `../server.properties`,
`MC_PORT` in `config.py`).

Several systemd **user** units are involved:

- **`mc-sleepd.service`** - the daemon (`mc_sleepd.py`) that watches everything.
  Meant to run permanently (starts at boot via `loginctl enable-linger`).
- **`mc-server.service`** - the actual Java/Forge server. Started and stopped
  automatically by `mc-sleepd`; you normally never touch this by hand. Has
  `CPUWeight=300`/`IOWeight=300` (vs. the default 100) so it's prioritized
  over other services sharing this host.
- **`mc-loginlog.service`** - independent login/chat/bot-attempt logger, see below.
- **`mc-web-export.timer`/`.service`** - periodically rebuilds the public
  status page, see below.

All commands below use `systemctl --user` (not plain `systemctl`) because
these are user-level units.

## Managing the services

Standard `systemctl --user` subcommands (`status`, `start`, `stop`,
`restart`, `enable`/`disable` for autostart at boot) and
`journalctl --user -u <unit> -f` (live logs) all work against any of the
units above.

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

## Login, chat, dimension-change, perf & bot-attempt logging

`mc-loginlog.service` (`login_logger.py`) tails both `mc-server` and
`mc-sleepd`'s console output (via journalctl, forced line-buffered with
`stdbuf -oL` - journalctl otherwise block-buffers its stdout once it's not a
tty, which can delay lines by up to a minute), independent of `mc-sleepd`'s
own state, and writes four CSVs:

- **`logins.csv`** - `timestamp,player,ip,status`, one row per event. `status` is:
  - `login` - a completed join
  - `attempt` - a login that never completed (e.g. mismatched mod list)
  - `bot` - a login attempt while sleeping from a client that never
    identified as Forge-modded, i.e. a server-list scanner - kicked without
    waking the server
  - `leave` - a player disconnecting
- **`chat.csv`** - `timestamp,player,message`, one row per in-game chat message.
- **`dimensions.csv`** - `timestamp,player,from,to`, one row per dimension
  change. Emitted as a `[dimchange] player=... from=... to=...` console line
  by the `MinecraftServerTool` mod (see `mod/`) on
  `PlayerChangedDimensionEvent`.
- **`perf.csv`** - `timestamp,dim,mspt,tps,chunks,entities`, one row per
  dimension per minute (plus one `dim=overall` row with `chunks`/`entities`
  left empty). Emitted by the same mod as `[perf] dim=... mspt=... tps=...
  [chunks=... entities=...]` console lines - meant for building TPS/MSPT
  graphs (e.g. for the website).

```bash
cat ~/minecraft/sleepd/logins.csv
cat ~/minecraft/sleepd/chat.csv
cat ~/minecraft/sleepd/dimensions.csv
cat ~/minecraft/sleepd/perf.csv
journalctl --user -u mc-loginlog -f   # watch new events live
```

All four files only ever append; nothing rotates or trims them, so clean
them up yourself if they grow large.

## Discord notifications

`notifier.py` mirrors every login-logger event (login, leave, attempt, chat
message, dimension change) to a Discord webhook - fully optional, silently does nothing if
`discord_webhook_url.txt` doesn't exist. To enable it, put the webhook URL as
the only line in `discord_webhook_url.txt` (`chmod 600`, gitignored - never
commit it).

`run_server.sh` also fires off `startup_notify.py` in the background on
every `mc-server` start, regardless of `mc-loginlog`: it tells apart a real
sleepd-triggered wake (which already gets its own "Server starting up
(player connecting)" notice from `mc_sleepd.py`, via a marker file
`wake_server()` drops right before starting the service) from a manual
`systemctl start` or a crash-restart, posting "Server started manually" for
those, then polls RCON and posts "Server is ready!" once the world's
actually loaded and joinable - useful since that can take several minutes
with this modpack.

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

Run manually with `python3 region_map.py`, or automatically as part of
`web_export.py` (see below) - output goes to `maps/` either way (gitignored
- regenerated from world data, not source).

## Public status page

`web_export.py` builds the public page at **https://nischhelm.com/mc/**
(note the trailing slash - `/mc` without it 404s, nginx's `alias` only
matches the exact prefix): one section per dimension with its `region_map.py`
image and TPS/MSPT charts built from `perf.csv`, plus an "overall" section.
`run_server.sh` backgrounds it (alongside `startup_notify.py`) on every
`mc-server` start, and `mc-web-export.timer` also re-runs it every 15
minutes so charts/maps/leaderboard stay reasonably current within a long
session too, not just as of the last restart.

Layout:

- `web/` - source, committed: `index.html`, `style.css`, `app.js` (vanilla,
  no build step, no external dependencies).
- `web/data/` - generated, gitignored: `perf.json` (perf.csv downsampled to
  one point/minute/dimension, last `config.WEB_PERF_HISTORY_DAYS` days, plus
  a playtime leaderboard, sorted by last login) and `maps/*.png` (copied
  from `../maps/`).

  The leaderboard pairs up `logins.csv`'s login/leave events per player. A
  session's end is whichever comes first: its "leave" (or the next login /
  now, for one that never got one) or the next `mc-server.service` restart
  after it started (`web_export.get_server_restarts()`, matched via
  systemd's `JOB_TYPE=start` job-done record rather than the log message
  text, which is locale-dependent). The restart case matters because a
  restart kills the connection without ever logging a "leave" - without
  capping there, a session left open by a crash/restart gets stitched
  together with whatever the player's *next*, unrelated login/leave pair
  was, potentially hours later, and counted as one continuous session.
- **`/var/www/mc-status`** - the actually-served copy. nginx runs as
  `www-data`, which can't traverse `/home/nischi` (mode `750`) at all, so
  `web_export.py`'s last step syncs all of `web/` (source + freshly
  generated `data/`) here every run. One-time setup, not automated (needs
  root):
  ```bash
  sudo mkdir -p /var/www/mc-status
  sudo chown nischi:nischi /var/www/mc-status
  sudo chmod 755 /var/www/mc-status
  ```
  plus a `location /mc/ { alias /var/www/mc-status/; try_files $uri $uri/ =404; }`
  block added to `/etc/nginx/sites-available/nischhelm`'s `443 ssl` server
  block, then `sudo nginx -t && sudo systemctl reload nginx`. After that,
  `web_export.py` never needs sudo again - it just writes into a directory
  it already owns.

Dimension names from the mod's perf/playerpos lines (`overworld`,
`the_nether`, ...) and `region_map.py`'s own labels (`overworld`, `nether`,
...) are matched up by normalizing both (lowercase, strip a `the_` prefix,
drop any `mod:` namespace prefix) rather than a hand-maintained ID table.

### Live status (online players, positions on the map)

Unlike the charts/maps above, "is the server awake" and "who's online right
now" need to be current *within* a session, not just as of the last
restart - so this part bypasses `web_export.py`'s restart-only cadence
entirely. `login_logger.py` writes `data/live.json` directly into
`/var/www/mc-status` on every relevant event (login, leave, each
`[playerpos]` tick) and re-checks `systemctl is-active mc-server` every 5s
during its normal sweep, independent of `mc_sleepd.py`'s own state tracking.
`app.js` polls it every 15s.

**Exact player coordinates are never written anywhere, public or otherwise.**
`login_logger.py` converts each `[playerpos] player=... dim=... x=... y=...
z=...` line into an approximate on-map percentage position (using
`config.MAP_LAYOUT_FILE`, the pixel geometry `web_export.py` writes on every
restart) and only keeps/publishes that percentage - resolution-limited to
the map's own pixel scale (~16 blocks/pixel), same as what's already visible
by looking at the image. The raw x/y/z values only ever exist transiently
inside `position_to_percent()`.

## MinecraftServerTool mod

`mod/` is a small standalone Forge mod (Gradle project, not built/installed
automatically - see its own directory for the build). Its `@Mod` annotation
sets `acceptableRemoteVersions = "*"` so vanilla clients (without the mod)
can still connect. It emits three kinds of plain console lines that
`login_logger.py` picks up:

- `[dimchange] player=... from=... to=...` on `PlayerChangedDimensionEvent`
  - consumed as `dimensions.csv` + Discord, see above.
- `[perf] dim=... mspt=... tps=... chunks=... entities=...` once a minute per
  dimension (plus one `dim=overall` line) - TPS/MSPT from
  `MinecraftServer.tickTimeArray`/`worldTickTimes`, loaded chunk count from
  `WorldServer.getChunkProvider().getLoadedChunkCount()`, loaded entity count
  from `WorldServer.loadedEntityList` - consumed as `perf.csv`, see above.
- `[playerpos] player=... dim=... x=... y=... z=...` once a minute per
  online player (same tick as `[perf]`) - consumed into the live status'
  on-map percentage positions, see above. Never written to a CSV.

Build with `./gradlew build` from `mod/` (needs the Forge 1.12.2 toolchain,
first run downloads and decompiles Minecraft - can take 10+ minutes), then
copy the resulting jar from `mod/build/libs/` into `../mods/`.

## What each file does

### Scripts

| File | Purpose |
|---|---|
| `mc_sleepd.py` | The daemon itself: SLEEPING → STARTING → RUNNING state machine, idle-timeout and 6h-restart logic |
| `mcproto.py` | Minimal Minecraft handshake/status/login-packet parsing (just enough to fake a "sleeping" server and detect real join attempts) |
| `rcon.py` | Minimal Source RCON client, used internally by `mc_sleepd.py` |
| `rcon_cli.py` | Standalone command-line tool for sending yourself RCON commands (see above) |
| `config.py` | All the tunable settings: ports, timeouts (20 min idle / 6h restart), messages, RCON/Discord paths |
| `run_server.sh` | The actual `java ...` invocation used as `mc-server.service`'s `ExecStart`; also backgrounds `startup_notify.py` and `web_export.py` |
| `startup_notify.py` | Posts "started manually" / "ready" Discord notices for every `mc-server` start, see above |
| `login_logger.py` | Tails the server console and appends every login/attempt/bot-probe/leave to `logins.csv`, every chat message to `chat.csv`, every dimension change to `dimensions.csv`, every perf sample to `perf.csv`; also maintains the live status (see above) |
| `notifier.py` | Posts login-logger and startup events to a Discord webhook, if configured |
| `region_map.py` | Renders per-dimension "recently touched" region maps (see above) |
| `web_export.py` | Builds the public status page (see above): runs `region_map.py`, downsamples `perf.csv` to `web/data/perf.json`, syncs everything to `/var/www/mc-status` |
| `web/` | Public status page: `index.html`/`style.css`/`app.js` source (committed) + `data/` (generated, gitignored) |
| `mod/` | `MinecraftServerTool` Forge mod source (Gradle project, see above) |
| `systemd/mc-server.service`, `systemd/mc-sleepd.service`, `systemd/mc-loginlog.service`, `systemd/mc-web-export.{service,timer}` | The canonical unit files, symlinked into `~/.config/systemd/user/` |

### Auto-generated files

| File | Purpose |
|---|---|
| `logins.csv`, `chat.csv`, `dimensions.csv`, `perf.csv` | The login/chat/dimension-change/perf history itself (see above); grows locally |
| `known_bots.txt` | Player names already announced once as scanner `bot` attempts, so Discord isn't spammed on repeats |
| `.sleepd_wake_marker` | Transient - dropped by `mc_sleepd.py` right before a wake-triggered start, deleted by `run_server.sh` on read; only ever exists for the few seconds between those two |
| `map_layout.json` | Internal, never published - region-map pixel geometry per dimension, written by `web_export.py`, read by `login_logger.py` for the live position percentage math (see above) |
| `maps/` | Output of `region_map.py`; regenerate anytime |

### Secrets

| File | Purpose |
|---|---|
| `rcon_password.txt` | Auto-generated RCON password (also written into `../server.properties`); `chmod 600` |
| `discord_webhook_url.txt` | Discord webhook URL for notifications (see above); `chmod 600` |
