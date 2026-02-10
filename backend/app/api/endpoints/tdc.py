from fastapi import APIRouter, HTTPException, Request

from app.services import tdc_rest
from app.services.tdc_digitalio import tdc_grpc_available
from app.services.tdc_token_manager import tdc_token_manager
from app.core.device_manager import device_manager

router = APIRouter()


@router.get("/status")
async def tdc_status(request: Request):
    cfg = device_manager.tdc_settings or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "ip_address": cfg.get("ip_address"),
        "port": cfg.get("port"),
        "encoder_port": cfg.get("encoder_port"),
        "grpc_available": tdc_grpc_available(),
        "token": tdc_token_manager.get_status(),
        "input_state": getattr(request.app.state, "tdc_input_state", None),
        "input_ts": getattr(request.app.state, "tdc_input_ts", None),
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
