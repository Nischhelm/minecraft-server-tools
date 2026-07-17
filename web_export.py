#!/usr/bin/env python3
"""Regenerates the public status page under web/: runs region_map.py, then
reads perf.csv, downsamples it to one point/minute/dimension over the last
config.WEB_PERF_HISTORY_DAYS days, builds a playtime leaderboard from
logins.csv, and writes web/data/perf.json alongside copies of the
region-map PNGs. web/ is nginx's docroot for the public page -
index.html/style.css/app.js are source (committed), web/data/ is entirely
generated (gitignored).

Also writes config.MAP_LAYOUT_FILE (internal, never published) - the pixel
geometry of each region-map image, which login_logger.py uses to turn a
live player's raw block position into an approximate on-map percentage
without ever exposing the exact coordinates.

Backgrounded by run_server.sh on every mc-server (re)start; also fine to
run by hand.
"""

import csv
import datetime
import json
import os
import shutil
import subprocess

import config
import region_map

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERF_CSV = os.path.join(BASE_DIR, "perf.csv")

DIMENSION_LABELS = [label for label, _ in config.MAP_DIMENSIONS]


def _normalize(name):
    """Matches perf.csv's "dim" values (the mod's DimensionType name, e.g.
    "the_nether", occasionally namespaced like "mymod:some_dim") up with
    region_map.py's independently-chosen MAP_DIMENSIONS labels (e.g.
    "nether") without a hand-maintained ID table."""
    name = name.rsplit(":", 1)[-1].lower()
    if name.startswith("the_"):
        name = name[len("the_"):]
    return name


NORMALIZED_TO_LABEL = {_normalize(label): label for label in DIMENSION_LABELS}


def load_perf_rows():
    try:
        with open(PERF_CSV, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def build_perf_series(rows):
    cutoff = datetime.datetime.now() - datetime.timedelta(days=config.WEB_PERF_HISTORY_DAYS)
    bucket_seconds = config.WEB_PERF_BUCKET_SECONDS

    # dim_label -> bucket_epoch -> {field: [values]}
    series = {}
    for row in rows:
        try:
            dt = datetime.datetime.fromisoformat(row["timestamp"])
        except ValueError:
            continue
        if dt < cutoff:
            continue

        dim_raw = row["dim"]
        dim = dim_raw if dim_raw == "overall" else NORMALIZED_TO_LABEL.get(_normalize(dim_raw), dim_raw)
        epoch = int(dt.timestamp())
        bucket = epoch - (epoch % bucket_seconds)
        entry = series.setdefault(dim, {}).setdefault(bucket, {"tps": [], "mspt": [], "chunks": [], "entities": []})
        entry["tps"].append(float(row["tps"]))
        entry["mspt"].append(float(row["mspt"]))
        if row["chunks"]:
            entry["chunks"].append(int(row["chunks"]))
        if row["entities"]:
            entry["entities"].append(int(row["entities"]))

    def avg(values):
        return round(sum(values) / len(values), 2) if values else None

    result = {}
    for dim, buckets in series.items():
        result[dim] = [
            {
                "t": bucket_epoch * 1000,  # ms since epoch, convenient for JS Date
                "tps": avg(v["tps"]),
                "mspt": avg(v["mspt"]),
                "chunks": avg(v["chunks"]),
                "entities": avg(v["entities"]),
            }
            for bucket_epoch, v in sorted(buckets.items())
        ]
    return result


def get_server_restarts():
    """Timestamps of every mc-server.service (re)start, oldest first -
    including Restart=on-failure auto-restarts after a crash (e.g. an
    OOM-kill). Matched on JOB_TYPE=start + USER_UNIT rather than MESSAGE
    text, since systemd localizes that message (seen in German on this
    host)."""
    cmd = ["journalctl"]
    if config.SYSTEMD_SCOPE:
        cmd.append(config.SYSTEMD_SCOPE)
    cmd.extend([
        "-u", config.SYSTEMD_UNIT, "--no-pager", "-q", "-o", "json",
        "--output-fields=__REALTIME_TIMESTAMP,JOB_TYPE,USER_UNIT,_COMM",
    ])
    result = subprocess.run(cmd, capture_output=True, text=True)

    restarts = []
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("_COMM") == "systemd" and entry.get("JOB_TYPE") == "start" and entry.get("USER_UNIT") == config.SYSTEMD_UNIT:
            restarts.append(datetime.datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000))
    restarts.sort()
    return restarts


def _first_restart_after(restarts, start, before):
    for restart in restarts:
        if start < restart < before:
            return restart
    return None


def build_leaderboard():
    """Total playtime and last-login per player from logins.csv's
    login/leave pairs. A real join always eventually produces a "leave"
    (however it phrases it) - *unless* the server itself gets force-
    restarted first, which kills the connection with no such line. So a
    session's true end is whichever comes first: its "leave" (or, lacking
    one, the next login for the same player / now, for a still-open
    session) or the next server restart after it started - capped at the
    restart, not discarded, since the connection genuinely was open until
    then."""
    try:
        with open(os.path.join(BASE_DIR, "logins.csv"), newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return []

    restarts = get_server_restarts()
    open_sessions = {}  # player -> login datetime
    totals_seconds = {}
    last_login = {}

    def close_session(name, start, end_bound):
        end = _first_restart_after(restarts, start, end_bound) or end_bound
        if end > start:
            totals_seconds[name] = totals_seconds.get(name, 0) + (end - start).total_seconds()

    for row in rows:
        if row["status"] not in ("login", "leave"):
            continue
        try:
            dt = datetime.datetime.fromisoformat(row["timestamp"])
        except ValueError:
            continue
        name = row["player"]
        if row["status"] == "login":
            if name in open_sessions:
                close_session(name, open_sessions[name], dt)
            open_sessions[name] = dt
            last_login[name] = dt
        else:
            start = open_sessions.pop(name, None)
            if start is not None:
                close_session(name, start, dt)

    now = datetime.datetime.now()
    for name, start in open_sessions.items():
        close_session(name, start, now)

    leaderboard = [
        {
            "player": name,
            "hours": round(totals_seconds.get(name, 0) / 3600, 1),
            "last_login": dt.isoformat(timespec="seconds"),
        }
        for name, dt in last_login.items()
    ]
    leaderboard.sort(key=lambda entry: entry["last_login"], reverse=True)
    return leaderboard


def write_map_layout(layouts):
    with open(config.MAP_LAYOUT_FILE, "w") as f:
        json.dump(layouts, f)


def copy_maps():
    os.makedirs(config.WEB_MAPS_DIR, exist_ok=True)
    for label in DIMENSION_LABELS:
        src = os.path.join(config.MAP_OUTPUT_DIR, f"{label}.png")
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(config.WEB_MAPS_DIR, f"{label}.png"))


def deploy():
    """Syncs web/ (source + freshly generated data/) to WEB_DEPLOY_DIR, the
    world-readable directory nginx actually serves (see config.py - nginx
    can't traverse into /home/nischi at all). Requires the one-time manual
    `mkdir`/`chown`/`chmod` setup from sleepd/README.md; skips with a
    warning (rather than crashing run_server.sh's background export) if
    that hasn't happened yet."""
    try:
        shutil.copytree(config.WEB_DIR, config.WEB_DEPLOY_DIR, dirs_exist_ok=True)
    except OSError as exc:
        print(f"web export: couldn't sync to {config.WEB_DEPLOY_DIR} ({exc}) - see README's one-time setup")


def main():
    layouts = region_map.main()
    write_map_layout(layouts)
    copy_maps()

    perf = build_perf_series(load_perf_rows())
    os.makedirs(config.WEB_DATA_DIR, exist_ok=True)
    manifest = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "dimensions": DIMENSION_LABELS,
        "perf": perf,
        "leaderboard": build_leaderboard(),
    }
    with open(os.path.join(config.WEB_DATA_DIR, "perf.json"), "w") as f:
        json.dump(manifest, f)

    deploy()

    total_points = sum(len(points) for points in perf.values())
    print(f"web export done: {total_points} perf point(s) across {len(perf)} dim(s)")


if __name__ == "__main__":
    main()
