from __future__ import annotations

import json
import os
import sys
import time

import paho.mqtt.client as mqtt
import requests


BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://backend:8000/api/v1").rstrip("/")
MQTT_HOST = os.getenv("MQTT_HOST", "mqtt-broker")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_ROOT = os.getenv("MQTT_TOPIC_ROOT", "picocan/pmu")
POLL_INTERVAL_S = float(os.getenv("POLL_INTERVAL_S", "1.0"))
NAMESPACE_URI = os.getenv("OPCUA_NAMESPACE_URI", "urn:picocan:mapping-unit")


def fetch_bundle() -> dict:
    response = requests.get(
        f"{BACKEND_BASE_URL}/integration/active-bundle",
        params={"topic_root": TOPIC_ROOT, "namespace_uri": NAMESPACE_URI},
        timeout=5.0,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="picocan-mqtt-publisher")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    last_hash: str | None = None
    print(f"[mqtt-publisher] connected to broker {MQTT_HOST}:{MQTT_PORT}", flush=True)
    while True:
        try:
            bundle = fetch_bundle()
            messages = bundle.get("mqtt_messages") or []
            bundle_hash = json.dumps(messages, sort_keys=True, ensure_ascii=True)
            if bundle_hash != last_hash:
                for item in messages:
                    topic = str(item.get("topic") or "").strip()
                    if not topic:
                        continue
                    payload = json.dumps(item.get("payload") or {}, ensure_ascii=False)
                    qos = int(item.get("qos", 1) or 1)
                    retain = bool(item.get("retain", True))
                    result = client.publish(topic, payload=payload, qos=qos, retain=retain)
                    result.wait_for_publish()
                print(
                    f"[mqtt-publisher] published {len(messages)} message(s), active_app={bundle.get('active_app')}",
                    flush=True,
                )
                last_hash = bundle_hash
        except Exception as exc:
            print(f"[mqtt-publisher] publish loop error: {exc}", file=sys.stderr, flush=True)
        time.sleep(max(0.2, POLL_INTERVAL_S))


if __name__ == "__main__":
    raise SystemExit(main())
