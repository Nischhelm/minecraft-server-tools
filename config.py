import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Public Minecraft port, see server.properties (server-port)
MC_HOST = "0.0.0.0"
MC_PORT = 25565
MC_PROTOCOL_VERSION = 335  # 1.12.2
MC_VERSION_NAME = "1.12.2"
MAX_PLAYERS = 15  # must match server.properties (max-players)

RCON_HOST = "127.0.0.1"
RCON_PORT = 25575
RCON_PASSWORD_FILE = os.path.join(BASE_DIR, "rcon_password.txt")

# Optional: a Discord webhook URL for wake/sleep/restart/login/attempt
# notifications. Notifications are silently skipped if this file is missing.
DISCORD_WEBHOOK_FILE = os.path.join(BASE_DIR, "discord_webhook_url.txt")

# Written by mc_sleepd.py just before it starts the server for a real player
# wake, so run_server.sh can tell that apart from a manual/admin start or a
# crash-restart and send a distinct Discord notice for those.
WAKE_MARKER_FILE = os.path.join(BASE_DIR, ".sleepd_wake_marker")

# Name of the systemd unit that starts/stops the real Minecraft server.
SYSTEMD_UNIT = "mc-server.service"
SYSTEMD_SCOPE = "--user"  # "" for system-wide units instead of user units

IDLE_TIMEOUT_SECONDS = 20 * 60
RESTART_INTERVAL_SECONDS = 6 * 60 * 60
RESTART_WARNING_OFFSETS_SECONDS = (300, 60)  # warn before restarting

POLL_INTERVAL_SECONDS = 60
STARTUP_POLL_INTERVAL_SECONDS = 5
STARTUP_TIMEOUT_SECONDS = 10 * 60  # 217 mods need time to boot

SLEEP_MOTD = (
    "§eServer is sleeping - join to wake it up§r\n"
    "§7Please wait ~1-2 minutes after your first connection attempt"
)
WAKE_KICK_MESSAGE = (
    "§aServer is starting!§r\n"
    "§7This can take 1-3 minutes with this modpack.\n"
    "§7Please reconnect in about 90 seconds."
)
VANILLA_KICK_MESSAGE = (
    "§cThis server requires the modpack to join.§r\n"
    "§7Vanilla clients can't connect."
)

# --- region_map.py ---
WORLD_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "world"))
MAP_OUTPUT_DIR = os.path.join(BASE_DIR, "maps")
# (label, region folder relative to WORLD_DIR). DIM44 deliberately excluded.
MAP_DIMENSIONS = (
    ("overworld", "region"),
    ("nether", "DIM-1/region"),
    ("end", "DIM1/region"),
    ("lost_cities", "LOST/region"),
)
MAP_SCALE = 32  # pixels per region side; 1:1 with a region's 32x32 chunks, so a future chunk-level view can subdivide each pixel
# Robust (median/MAD-based) outlier rejection - some region files end up with
# wild coordinates from mob/entity position bugs (seen: r.4194303.4194303.mca).
# Those would blow up the bounding box to millions of pixels, so they're
# excluded from the image and reported separately instead.
MAP_OUTLIER_MAD_MULTIPLIER = 5
MAP_OUTLIER_MIN_THRESHOLD = 80  # regions; floor so normally-spread-out worlds aren't over-filtered

# --- web_export.py ---
WEB_DIR = os.path.join(BASE_DIR, "web")  # source (index.html/style.css/app.js), committed
WEB_DATA_DIR = os.path.join(WEB_DIR, "data")
WEB_MAPS_DIR = os.path.join(WEB_DATA_DIR, "maps")
WEB_PERF_HISTORY_DAYS = 3
# The mod already samples once/minute per dimension - bucket at that same
# 60s so nothing gets thrown away (this just absorbs multi-dimension jitter
# within the same minute rather than actually downsampling).
WEB_PERF_BUCKET_SECONDS = 60

# nginx (running as www-data) can't traverse /home/nischi (mode 750), so the
# actually-served copy lives in this separate, world-readable directory -
# web_export.py syncs source + generated data here on every run. One-time
# setup (`mkdir`/`chown`/`chmod`) is manual, see sleepd/README.md.
WEB_DEPLOY_DIR = "/var/www/mc-status"
