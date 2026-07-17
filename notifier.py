"""Sends event notifications to a Discord webhook, if one is configured.

Silently does nothing when config.DISCORD_WEBHOOK_FILE doesn't exist, so this
stays fully optional.
"""

import json
import logging
import urllib.request

import config

log = logging.getLogger("mc-sleepd")


def _webhook_url():
    try:
        with open(config.DISCORD_WEBHOOK_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def notify(message):
    url = _webhook_url()
    if not url:
        return
    body = json.dumps({"content": message}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "mc-sleepd (+https://discord.com/developers/docs/resources/webhook)"}
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as exc:
        log.warning("Discord notification failed: %s", exc)
