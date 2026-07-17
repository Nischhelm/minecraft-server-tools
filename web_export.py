#!/usr/bin/env python3
"""Regenerates the public status page under web/: runs region_map.py, then
reads perf.csv, downsamples it into 15-minute buckets per dimension, and
writes web/data/perf.json alongside copies of the region-map PNGs. web/ is
nginx's docroot for the public page - index.html/style.css/app.js are
source (committed), web/data/ is entirely generated (gitignored).

Meant to run periodically via mc-web-export.timer; also fine to run by hand.
"""

import csv
import datetime
import json
import os
import shutil

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
    region_map.main()
    copy_maps()

    perf = build_perf_series(load_perf_rows())
    os.makedirs(config.WEB_DATA_DIR, exist_ok=True)
    manifest = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "dimensions": DIMENSION_LABELS,
        "perf": perf,
    }
    with open(os.path.join(config.WEB_DATA_DIR, "perf.json"), "w") as f:
        json.dump(manifest, f)

    deploy()

    total_points = sum(len(points) for points in perf.values())
    print(f"web export done: {total_points} perf point(s) across {len(perf)} dim(s)")


if __name__ == "__main__":
    main()
