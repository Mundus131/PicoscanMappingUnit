import time
from fastapi import APIRouter, HTTPException, Request

from app.services import tdc_rest
from app.services.tdc_digitalio import tdc_grpc_available, tdc_digitalio_client
from app.services.tdc_token_manager import tdc_token_manager
from app.core.device_manager import device_manager

router = APIRouter()


@router.get("/status")
async def tdc_status(request: Request):
    cfg = device_manager.tdc_settings or {}
    session = getattr(request.app.state, "acquisition_session", None) or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "ip_address": cfg.get("ip_address"),
        "port": cfg.get("port"),
        "encoder_port": cfg.get("encoder_port"),
        "grpc_available": tdc_grpc_available(),
        "token": tdc_token_manager.get_status(),
        "input_state": getattr(request.app.state, "tdc_input_state", None),
        "input_ts": getattr(request.app.state, "tdc_input_ts", None),
        "encoder_rpm": session.get("encoder_rpm"),
        "encoder_speed_mps": session.get("encoder_speed_mps"),
        "poll_interval_ms": cfg.get("poll_interval_ms"),
    }


@router.get("/encoder/config")
async def encoder_config():
    try:
        url = tdc_rest.build_process_url(tdc_rest._get_base_config()["encoder_port"], data_format="iodd")
        return {
            "encoder_port": tdc_rest._get_base_config()["encoder_port"],
            "device_alias": tdc_rest._resolve_device_alias(tdc_rest._get_base_config()["encoder_port"]),
            "process_url": url,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/encoder/process-data")
async def encoder_process_data(format: str = "iodd"):
    try:
        return tdc_rest.fetch_encoder_process_data(data_format=format)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/io-state")
async def io_state():
    cfg = device_manager.tdc_settings or {}
    enabled = bool(cfg.get("enabled", False))
    available = tdc_grpc_available()
    preferred_names = ["DI_A", "DI_B", "DI_C", "DI_D", "DIO_A", "DIO_B", "DIO_C", "DIO_D"]
    devices = []
    states = {}
    read_ts = time.time()

    if enabled and available:
        try:
            listed = tdc_digitalio_client.list_devices() or []
            for item in listed:
                name = getattr(item, "name", None)
                if not name:
                    continue
                devices.append(
                    {
                        "name": name,
                        "type": int(getattr(item, "type", -1)),
                        "direction": int(getattr(item, "direction", -1)),
                    }
                )
        except Exception:
            devices = []

    names = [d["name"] for d in devices] if devices else preferred_names
    # Keep deterministic order and unique names.
    names = list(dict.fromkeys(names))

    for name in names:
        state = None
        if enabled and available:
            try:
                state = tdc_digitalio_client.read(name)
            except Exception:
                state = None
        states[name] = {
            "state": state,
            "label": "HIGH" if state == 2 else "LOW" if state == 1 else "UNKNOWN",
        }

    return {
        "enabled": enabled,
        "grpc_available": available,
        "read_ts": read_ts,
        "devices": devices,
        "states": states,
    }
