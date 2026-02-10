import logging
from typing import Any, Dict

import requests

from app.core.device_manager import device_manager
from app.services.tdc_token_manager import tdc_token_manager

logger = logging.getLogger(__name__)


def _get_base_config() -> dict:
    cfg = device_manager.tdc_settings or {}
    return {
        "ip_address": cfg.get("ip_address", "192.168.0.100"),
        "encoder_port": str(cfg.get("encoder_port", "1")),
    }


def _resolve_device_alias(port: str) -> str:
    # TDC default naming like in COMO
    return f"master1port{port}"


def build_process_url(port: str, data_format: str = "iodd") -> str:
    cfg = _get_base_config()
    alias = _resolve_device_alias(port)
    fmt = "iodd" if data_format == "iodd" else "byteArray"
    return f"http://{cfg['ip_address']}/iolink/v1/devices/{alias}/processdata/value?format={fmt}"


def fetch_encoder_process_data(data_format: str = "iodd") -> Dict[str, Any]:
    cfg = _get_base_config()
    port = cfg["encoder_port"]
    url = build_process_url(port, data_format=data_format)
    token = tdc_token_manager.get_token()
    if not token:
        raise RuntimeError("TDC token unavailable")
    resp = requests.get(
        url,
        headers={
            "Cookie": f"access_token={token}",
            "User-Agent": "Picoscan-TDC/1.0",
            "Content-Type": "application/json",
        },
        timeout=5,
    )
    if not resp.ok:
        raise RuntimeError(f"TDC HTTP {resp.status_code}: {resp.text}")
    return {
        "port": port,
        "device_alias": _resolve_device_alias(port),
        "format": data_format,
        "data": resp.json(),
    }
