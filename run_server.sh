#!/bin/bash
# ExecStart script for mc-server.service. Same java invocation as startup.sh,
# but without its crash-restart loop - systemd handles that (Restart=on-failure).
# startup.sh stays unchanged for manual/tmux operation.
set -e
cd "$(dirname "$0")/.."

exec java -Dforge.logging.console.level=info -Dmixin.debug.export=true -Xms6G -Xmx6G \
    -XX:SurvivorRatio=32 -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 \
    -XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:G1HeapWastePercent=5 \
    -XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 \
    -XX:G1MixedGCLiveThresholdPercent=90 -XX:G1RSetUpdatingPauseTimePercent=5 \
    -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1 \
    -jar "twitch_rlcraft_2.9.3.jar" nogui
