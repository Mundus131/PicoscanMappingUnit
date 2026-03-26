from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Callable

import paho.mqtt.client as mqtt

from app.services.integration_mapper import DEFAULT_NAMESPACE_URI, DEFAULT_TOPIC_ROOT, build_active_bundle


logger = logging.getLogger(__name__)


class BackendMqttPublisher:
    def __init__(
        self,
        session_provider: Callable[[], dict],
        availability_provider: Callable[[], dict],
        tdc_label_provider: Callable[[], str],
        active_app_provider: Callable[[], str],
    ):
        self._session_provider = session_provider
        self._availability_provider = availability_provider
        self._tdc_label_provider = tdc_label_provider
        self._active_app_provider = active_app_provider
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: mqtt.Client | None = None
        self._last_hash: str | None = None
        self._enabled = str(os.getenv("MQTT_PUBLISH_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
        self._host = os.getenv("MQTT_HOST", "127.0.0.1")
        self._port = int(os.getenv("MQTT_PORT", "1883"))
        self._topic_root = os.getenv("MQTT_TOPIC_ROOT", DEFAULT_TOPIC_ROOT)
        self._namespace_uri = os.getenv("OPCUA_NAMESPACE_URI", DEFAULT_NAMESPACE_URI)
        self._poll_interval_s = max(0.2, float(os.getenv("MQTT_PUBLISH_INTERVAL_S", "1.0") or "1.0"))
        self._last_publish_ts_ms: int | None = None
        self._last_error: str | None = None
        self._connected = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self):
        if not self._enabled:
            logger.info("MQTT publisher disabled by configuration")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mqtt-publisher")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._client:
            try:
                self._client.loop_stop()
            except Exception:
                pass
            try:
                self._client.disconnect()
            except Exception:
                pass

    def _connect_client(self) -> mqtt.Client:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="picocan-backend-mqtt")
        client.connect(self._host, self._port, keepalive=30)
        client.loop_start()
        self._connected = True
        logger.info("MQTT publisher connected to broker %s:%s", self._host, self._port)
        return client

    def get_status(self) -> dict:
        broker_reachable = False
        try:
            with socket.create_connection((self._host, self._port), timeout=1.0):
                broker_reachable = True
        except Exception:
            broker_reachable = False
        return {
            "enabled": self._enabled,
            "broker_host": self._host,
            "broker_port": self._port,
            "topic_root": self._topic_root,
            "namespace_uri": self._namespace_uri,
            "poll_interval_s": self._poll_interval_s,
            "connected": self._connected,
            "broker_reachable": broker_reachable,
            "last_publish_ts_ms": self._last_publish_ts_ms,
            "last_error": self._last_error,
        }

    def _build_bundle(self) -> dict:
        session = self._session_provider() or {}
        availability = self._availability_provider() or {}
        tdc_label = self._tdc_label_provider()
        active_app = str(self._active_app_provider() or "none")
        return build_active_bundle(
            session,
            availability,
            tdc_label,
            active_app=active_app,
            topic_root=self._topic_root,
            namespace_uri=self._namespace_uri,
        )

    def _run(self):
        while not self._stop_event.is_set():
            try:
                if self._client is None:
                    self._client = self._connect_client()
                bundle = self._build_bundle()
                messages = bundle.get("mqtt_messages") or []
                bundle_hash = json.dumps(messages, sort_keys=True, ensure_ascii=True)
                if bundle_hash != self._last_hash:
                    for item in messages:
                        topic = str(item.get("topic") or "").strip()
                        if not topic:
                            continue
                        payload = json.dumps(item.get("payload") or {}, ensure_ascii=False)
                        qos = int(item.get("qos", 1) or 1)
                        retain = bool(item.get("retain", True))
                        info = self._client.publish(topic, payload=payload, qos=qos, retain=retain)
                        info.wait_for_publish()
                    self._last_hash = bundle_hash
                    self._last_publish_ts_ms = int(time.time() * 1000)
                    self._last_error = None
                    logger.info(
                        "MQTT publisher sent %s message(s), active_app=%s",
                        len(messages),
                        bundle.get("active_app"),
                    )
            except Exception as exc:
                logger.warning("MQTT publish loop error: %s", exc)
                self._last_hash = None
                self._last_error = str(exc)
                self._connected = False
                if self._client:
                    try:
                        self._client.loop_stop()
                    except Exception:
                        pass
                    try:
                        self._client.disconnect()
                    except Exception:
                        pass
                self._client = None
            self._stop_event.wait(self._poll_interval_s)
