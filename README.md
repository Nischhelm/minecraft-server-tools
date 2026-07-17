# sleepd - Sleep/Wake System for the RLCraft Server

Runs the Minecraft server (`mc-server.service`) on demand instead of 24/7:
sleeping when nobody's around, waking on a real join attempt, sleeping again
after 20 minutes with no players, and force-restarting every 6 hours with an
in-game warning. See `/home/nischi/.claude/plans/zippy-sleeping-muffin.md`
for the original design rationale.

Players connect via `nischhelm.com` - no port needed, the server runs on
Minecraft's default port 25565 (`server-port` in `../server.properties`,
`MC_PORT` in `config.py`).

Three systemd **user** units are involved:

- **`mc-sleepd.service`** - the daemon (`mc_sleepd.py`) that watches everything.
  Meant to run permanently (starts at boot via `loginctl enable-linger`).
- **`mc-server.service`** - the actual Java/Forge server. Started and stopped
  automatically by `mc-sleepd`; you normally never touch this by hand. Has
  `CPUWeight=300`/`IOWeight=300` (vs. the default 100) so it's prioritized
  over the Nextcloud stack (`snap.nextcloud.*.service`) that shares this host.
- **`mc-loginlog.service`** - independent login logger, see below.

All commands below use `systemctl --user` (not plain `systemctl`) because
these are user-level units.

## Everyday commands

| What | Command |
|---|---|
| Check what's currently happening | `systemctl --user status mc-sleepd` |
| Watch the sleep/wake daemon live | `journalctl --user -u mc-sleepd -f` |
| Watch the Minecraft server console live | `journalctl --user -u mc-server -f` |
| Manually stop the Minecraft server (saves + exits cleanly) | `systemctl --user stop mc-server` |
| Manually start the Minecraft server right now (skip waiting for a login) | `systemctl --user start mc-server` |
| Manually force a restart right now | `systemctl --user restart mc-server` |

`stop`/`start`/`restart` on `mc-server` are safe to run yourself any time -
`mc-sleepd` just reacts to whatever state it finds (goes back to sleep once
the server stops, does nothing if you start it directly).

## Turning the whole sleep/wake system on or off

| What | Command |
|---|---|
| Pause everything (server won't wake on join anymore, current server keeps running if it's up) | `systemctl --user stop mc-sleepd` |
| Resume it | `systemctl --user start mc-sleepd` |
| Disable autostart at boot (survives until you re-enable) | `systemctl --user disable mc-sleepd` |
| Re-enable autostart at boot | `systemctl --user enable --now mc-sleepd` |
| Fully undo the boot-persistence (only if you want the whole thing gone) | `loginctl disable-linger nischi` |

If you disable `mc-sleepd` entirely and want the server reachable the old
way, start it directly with `systemctl --user start mc-server` (or fall back
to the original `../startup.sh` in a tmux session - untouched, still works).

## Talking to the running server (RCON)

No need to install `mcrcon` or anything else - there's a tiny built-in CLI:

```bash
cd ~/minecraft/sleepd
python3 rcon_cli.py list
python3 rcon_cli.py say Hello everyone!
python3 rcon_cli.py "give Nischhelm minecraft:diamond 5"
```

It reads the password from `rcon_password.txt` automatically. Only works
while `mc-server` is actually running (RCON comes up once the world has
loaded, near the end of boot). Note there's no interactive stdin console
anymore like the old tmux window had - this is the replacement for typing
commands directly.

## Login history (IP, player, timestamp)

`mc-loginlog.service` tails the Minecraft server's console (via journalctl)
and appends one row per login to `logins.csv` (`timestamp,player,ip`),
independent of `mc-sleepd`'s own state - it catches every real login, not
just the one that wakes the server up.

```bash
cat ~/minecraft/sleepd/logins.csv
journalctl --user -u mc-loginlog -f   # watch new logins live
```

It only ever appends; nothing rotates or trims it, so clean it up yourself
if it grows large.

## What each file does

| File | Purpose |
|---|---|
| `mc_sleepd.py` | The daemon itself: SLEEPING → STARTING → RUNNING state machine, idle-timeout and 6h-restart logic |
| `mcproto.py` | Minimal Minecraft handshake/status/login-packet parsing (just enough to fake a "sleeping" server and detect real join attempts) |
| `rcon.py` | Minimal Source RCON client, used internally by `mc_sleepd.py` |
| `rcon_cli.py` | Standalone command-line tool for sending yourself RCON commands (see above) |
| `config.py` | All the tunable settings: ports, timeouts (20 min idle / 6h restart), messages, RCON path |
| `run_server.sh` | The actual `java ...` invocation used as `mc-server.service`'s `ExecStart` (same flags as `../startup.sh`, just without its restart loop - systemd handles restarts now) |
| `rcon_password.txt` | Auto-generated RCON password (also written into `../server.properties`); `chmod 600`, keep it that way |
| `login_logger.py` | Tails the server console and appends every login (timestamp, player, IP) to `logins.csv` |
| `logins.csv` | The login history itself (see above) |
| `systemd/mc-server.service`, `systemd/mc-sleepd.service`, `systemd/mc-loginlog.service` | The canonical unit files, symlinked into `~/.config/systemd/user/` |

## Changing behavior

Idle timeout, restart interval, warning timing, sleep MOTD, and the wake-kick
message are all constants at the top of `config.py` - edit and
`systemctl --user restart mc-sleepd` to apply. No need to touch `mc_sleepd.py`
itself for those kinds of tweaks.
