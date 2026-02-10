import logging
import threading
import time
from typing import Optional

import requests

from app.core.device_manager import device_manager

logger = logging.getLogger(__name__)


class TdcTokenManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._expires_at: Optional[float] = None
        self._config_cache: dict = {}

    def _get_config(self) -> dict:
        cfg = device_manager.tdc_settings or {}
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "ip_address": cfg.get("ip_address", "192.168.0.100"),
            "login": cfg.get("login", "admin"),
            "password": cfg.get("password", "Welcome1!"),
            "realm": cfg.get("realm", "admin"),
            "token_refresh_interval_s": float(cfg.get("token_refresh_interval_s", 300)),
        }

    def _needs_refresh(self, now: float) -> bool:
        if not self._token or not self._expires_at:
            return True
        return now >= self._expires_at

    def refresh(self) -> Optional[str]:
        config = self._get_config()
        if not config.get("enabled"):
            return None

        login_url = f"http://{config['ip_address']}/auth/login"
        payload = {
            "username": config["login"],
            "password": config["password"],
            "realm": config["realm"],
        }

        try:
            resp = requests.post(
                login_url,
                json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json() or {}
            token = data.get("token")
            if not token:
                raise RuntimeError("Missing token in response")
            now = time.time()
            refresh_interval = max(30.0, float(config.get("token_refresh_interval_s", 300)))
            self._token = token
            # Refresh a bit earlier than interval
            self._expires_at = now + refresh_interval - 10.0
            self._config_cache = config
            logger.info("TDC token refreshed")
            return token
        except Exception as exc:
            logger.warning("TDC token refresh failed: %s", exc)
            return None

    def get_token(self) -> Optional[str]:
        with self._lock:
            now = time.time()
            config = self._get_config()
            if config != self._config_cache:
                self._token = None
                self._expires_at = None
                self._config_cache = config
            if self._needs_refresh(now):
                return self.refresh()
            return self._token

    def get_status(self) -> dict:
        return {
            "has_token": bool(self._token),
            "expires_at": self._expires_at,
            "config": {
                "ip_address": self._config_cache.get("ip_address"),
                "login": self._config_cache.get("login"),
                "realm": self._config_cache.get("realm"),
            },
        }


tdc_token_manager = TdcTokenManager()
