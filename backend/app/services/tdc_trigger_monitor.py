import logging
import threading
import time

from app.core.device_manager import device_manager
from app.services.tdc_digitalio import tdc_digitalio_client, tdc_grpc_available
from app.api.endpoints import acquisition as acquisition_api

logger = logging.getLogger(__name__)


class TdcTriggerMonitor:
    def __init__(self, app):
        self.app = app
        self._stop_event = threading.Event()
        self._thread = None
        self._last_state = None
        self._config_cache = {}
        self._last_read_ts = None
        self._pending_start_at = None
        self._pending_stop_at = None
        self._pending_stop_target_mm = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _get_config(self) -> dict:
        cfg = device_manager.tdc_settings or {}
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "trigger_input": cfg.get("trigger_input", "DI1"),
            "poll_interval_ms": int(cfg.get("poll_interval_ms", 200)),
            "start_delay_mode": cfg.get("start_delay_mode", "time"),
            "start_delay_ms": float(cfg.get("start_delay_ms", 0.0) or 0.0),
            "start_delay_mm": float(cfg.get("start_delay_mm", 0.0) or 0.0),
            "stop_delay_mode": cfg.get("stop_delay_mode", "time"),
            "stop_delay_ms": float(cfg.get("stop_delay_ms", 0.0) or 0.0),
            "stop_delay_mm": float(cfg.get("stop_delay_mm", 0.0) or 0.0),
        }

    def _run(self):
        while not self._stop_event.is_set():
            config = self._get_config()
            if not config.get("enabled"):
                self._last_state = None
                self._pending_start_at = None
                self._pending_stop_at = None
                self._pending_stop_target_mm = None
                time.sleep(0.5)
                continue
            if not tdc_grpc_available():
                time.sleep(0.5)
                continue

            poll_interval = max(50, int(config.get("poll_interval_ms", 200)))
            input_name = config.get("trigger_input") or "DI1"

            try:
                state = tdc_digitalio_client.read(input_name)
            except Exception as exc:
                logger.warning("TDC monitor read error: %s", exc)
                state = None

            if state is not None:
                self.app.state.tdc_input_state = state
                self.app.state.tdc_input_ts = time.time()
                self._last_read_ts = self.app.state.tdc_input_ts
                session = acquisition_api._get_session_from_app(self.app)
                recording = bool(session.get("recording"))
                now = time.time()

                # IOState: 1=LOW, 2=HIGH
                if state == 2:
                    # Cancel pending stop when trigger is high again
                    self._pending_stop_at = None
                    self._pending_stop_target_mm = None

                    if not recording:
                        if config["start_delay_mode"] == "time" and config["start_delay_ms"] > 0:
                            if self._pending_start_at is None:
                                self._pending_start_at = now + (config["start_delay_ms"] / 1000.0)
                            elif now >= self._pending_start_at:
                                start_session = acquisition_api.start_trigger_session(self.app)
                                start_session["trigger_source"] = "tdc"
                                self._pending_start_at = None
                        else:
                            start_session = acquisition_api.start_trigger_session(self.app)
                            start_session["trigger_source"] = "tdc"
                            if config["start_delay_mode"] == "distance" and config["start_delay_mm"] > 0:
                                start_session["start_delay_mm_remaining"] = float(config["start_delay_mm"])
                            self._pending_start_at = None
                elif state == 1:
                    # Cancel pending start when trigger is low
                    self._pending_start_at = None

                    if recording:
                        if config["stop_delay_mode"] == "time" and config["stop_delay_ms"] > 0:
                            if self._pending_stop_at is None:
                                self._pending_stop_at = now + (config["stop_delay_ms"] / 1000.0)
                            elif now >= self._pending_stop_at:
                                acquisition_api.stop_trigger_session(self.app)
                                acquisition_api._get_session_from_app(self.app)["trigger_source"] = "tdc"
                                self._pending_stop_at = None
                        elif config["stop_delay_mode"] == "distance" and config["stop_delay_mm"] > 0:
                            if self._pending_stop_target_mm is None:
                                self._pending_stop_target_mm = float(session.get("distance_mm", 0.0)) + float(config["stop_delay_mm"])
                            elif float(session.get("distance_mm", 0.0)) >= float(self._pending_stop_target_mm):
                                acquisition_api.stop_trigger_session(self.app)
                                acquisition_api._get_session_from_app(self.app)["trigger_source"] = "tdc"
                                self._pending_stop_target_mm = None
                        else:
                            acquisition_api.stop_trigger_session(self.app)
                            acquisition_api._get_session_from_app(self.app)["trigger_source"] = "tdc"
                            self._pending_stop_at = None
                            self._pending_stop_target_mm = None
                    else:
                        self._pending_stop_at = None
                        self._pending_stop_target_mm = None

                self._last_state = state

            time.sleep(poll_interval / 1000.0)
