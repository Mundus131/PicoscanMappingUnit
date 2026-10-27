# Update an existing TDC deployment

These files are for Docker Compose / a **Portainer standalone stack**, not Swarm.
The native SDK probe is not enabled in production: this release keeps the Python
receiver and includes its tested frame-ordering correction.

## Before deployment

Wait for all GitHub Actions jobs for the desired commit to succeed. All three
application images must have `sha-<full commit hash>` tags. Do not substitute an
older `arm64` image if a build fails.

Schedule a maintenance window: no log may be moving through the scanner, no active
recording or pending measurement, and no operator changing configuration. Save the
current stack YAML and environment in Portainer. Keep the old images for rollback.

If SSH reports **REMOTE HOST IDENTIFICATION HAS CHANGED**, stop and verify the
fingerprint using the TDC's trusted local console or its administrator. Do not
disable StrictHostKeyChecking or remove the known_hosts entry without verification.

Copy these files to a private directory on the TDC:

- `docker-compose.tdc.registry.yml` from the repository root
- `prepare-tdc.sh` from this directory

Run there:

```sh
sh prepare-tdc.sh sha-FULL_COMMIT_HASH
```

This creates `tdc.env` with the actual existing volume names, a private configuration
backup and previous image IDs. It copies the active device/TDC/calibration settings
into `/app/data/picoscans_config.json`, retaining them across container replacement.
It does not change IP addresses, trigger settings, segment counts or the scanner.
The backup may contain passwords: do not commit or share it.

## Portainer (recommended for an existing Portainer stack)

Update the **same existing stack**, rather than creating another stack with the
same container names. Replace the editor content with `docker-compose.tdc.registry.yml`.
Load `tdc.env` in the stack's environment variables section (or enter its values).
Use the option to pull/re-pull images during the stack update. No server-side bind
mounts or `env_file` paths are required.

MQTT is intentionally bound to loopback only. Existing external MQTT subscribers
will not connect to this stack until authenticated LAN access is configured.
HTTP 3000/8000 and OPC UA 4840 remain reachable on the host: restrict them to the
trusted industrial LAN. This stack does not add authentication/TLS to those services.

## CLI alternative

Do not mix CLI ownership with a Portainer-managed deployment. If using CLI, use
the original Compose project name saved in `backup-*/compose-project.txt`:

```sh
docker compose --env-file tdc.env -p ORIGINAL_PROJECT -f docker-compose.tdc.registry.yml config --quiet
docker compose --env-file tdc.env -p ORIGINAL_PROJECT -f docker-compose.tdc.registry.yml pull
# Only after every pull succeeds:
docker compose --env-file tdc.env -p ORIGINAL_PROJECT -f docker-compose.tdc.registry.yml up -d
docker compose --env-file tdc.env -p ORIGINAL_PROJECT -f docker-compose.tdc.registry.yml ps
docker logs --tail 100 picocan-backend
```

Never use `down -v` or delete the external volumes.

## Validate and roll back

```sh
curl -f http://127.0.0.1:8000/api/v1/acquisition/devices/availability
curl -f http://127.0.0.1:8000/api/v1/acquisition/trigger/status
```

Open `http://192.168.0.100:3000`. Verify saved scanner IPs/destinations, IODD/Velocity,
encoder speed, trigger state and delay settings. The PC test at `192.168.0.250`
does not establish the correct sensor destination or segment count for the TDC.
Run a known-length sample and inspect measurement quality before production use.

If validation fails, stop measurements and restore the old stack YAML/environment
using the saved old images; do not erase the data volumes. The prior container-local
configuration may have been lost on recreation: restore the saved JSON into the
old configuration location, or use the persistent data path with the old backend.
