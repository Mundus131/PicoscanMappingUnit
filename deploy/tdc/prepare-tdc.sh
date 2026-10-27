#!/bin/sh
# Run on the TDC, in a maintenance window; never reads SSH credentials.
set -eu
umask 077
tag=${1:?Usage: sh prepare-tdc.sh sha-FULL_COMMIT_HASH}
hash=${tag#sha-}
[ "$tag" != "$hash" ] && [ "${#hash}" -eq 40 ] || { echo 'Expected sha- followed by a full 40-character commit'; exit 1; }
case "$hash" in *[!0-9a-f]*) echo 'Invalid commit hash'; exit 1;; esac
[ ! -e tdc.env ] || { echo 'tdc.env already exists; preserve it before preparing another deployment'; exit 1; }
data=$(docker inspect picocan-backend --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')
logs=$(docker inspect picocan-backend --format '{{range .Mounts}}{{if eq .Destination "/app/logs"}}{{.Name}}{{end}}{{end}}')
[ -n "$data" ] && [ -n "$logs" ] || { echo 'Expected named data/log volumes; inspect the current stack manually'; exit 1; }
docker volume inspect "$data" "$logs" >/dev/null
# Refuse migration during recording or unfinished capture processing.
docker exec picocan-backend python -c 'import json, urllib.request; s=json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/v1/acquisition/trigger/status", timeout=10)); assert s.get("recording") is False and not s.get("analysis_pending", False), "Capture still active or status unknown"'
backup="backup-$(date +%Y%m%d-%H%M%S)"
mkdir "$backup"
source=$(docker exec picocan-backend python -c 'import os; from config.settings import settings; print(os.path.abspath(settings.picoscan_devices_config))')
docker cp "picocan-backend:$source" "$backup/picoscans_config.json"
# Keep previous image IDs for rollback even if a mutable registry tag changes.
for c in picocan-backend picocan-frontend picocan-opcua-server picocan-mqtt-broker; do
  docker inspect "$c" --format '{{.Name}} {{.Image}}' >> "$backup/images.txt"
done
docker inspect picocan-backend --format '{{index .Config.Labels "com.docker.compose.project"}}' > "$backup/compose-project.txt"
docker exec picocan-backend python -c 'import json, os, shutil, time; from config.settings import settings; src=os.path.abspath(settings.picoscan_devices_config); dst="/app/data/picoscans_config.json"; json.load(open(src)); same=os.path.realpath(src)==os.path.realpath(dst); backup=dst+".before-deploy-"+str(time.time_ns()); shutil.copy2(dst, backup) if os.path.exists(dst) and not same else None; shutil.copy2(src, dst) if not same else None; print("Persistent configuration ready")'
cat >tdc.env <<EOF
IMAGE_TAG=$tag
BACKEND_DATA_VOLUME=$data
BACKEND_LOGS_VOLUME=$logs
TDC_IP=192.168.0.100
LOG_LEVEL=INFO
EOF
echo "Prepared tdc.env and $backup. Nothing was stopped or redeployed."
echo 'Configuration backups may contain TDC credentials. Keep private; do not upload.'
