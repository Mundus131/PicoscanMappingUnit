from fastapi import APIRouter, HTTPException, Request
from typing import List
from app.core.device_manager import device_manager
from app.schemas.device import DeviceResponse, DeviceCreate, DeviceUpdate
import logging
import subprocess
import socket
import platform
import shutil

logger = logging.getLogger(__name__)
router = APIRouter()
PICOSCAN_PORT_MIN = 2115
PICOSCAN_PORT_MAX = 2118


def _icmp_ping(ip: str, timeout_ms: int = 500) -> bool:
    """Best-effort ICMP ping on current OS.

    Returns False when ping binary is unavailable or probe fails.
    """
    ping_cmd = shutil.which("ping")
    if not ping_cmd:
        return False
    try:
        system = platform.system().lower()
        if system == "windows":
            # Windows: -n count, -w timeout_ms
            args = [ping_cmd, "-n", "1", "-w", str(int(timeout_ms)), ip]
        else:
            # Linux/macOS: -c count, -W timeout_seconds
            timeout_s = max(1, int(round(timeout_ms / 1000.0)))
            args = [ping_cmd, "-c", "1", "-W", str(timeout_s), ip]
        result = subprocess.run(args, capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False

def _sync_device_listener(request: Request, device_id: str) -> None:
    """Start/stop/restart receiver for a device after config changes."""
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    if receiver_manager is None:
        return

    device = device_manager.get_device(device_id)
    if not device:
        return

    try:
        if device_id in receiver_manager.receivers:
            receiver_manager.stop_listening(device_id)
    except Exception as exc:
        logger.warning("Failed to stop existing listener for %s: %s", device_id, exc)

    if not bool(getattr(device, "enabled", True)):
        return

    ok = receiver_manager.start_listening(
        device.device_id,
        "0.0.0.0",
        device.port,
        segments_per_scan=getattr(device, "segments_per_scan", None),
        format_type=getattr(device, "format_type", "compact"),
        device_type=getattr(device, "device_type", "picoscan"),
        sensor_ip=getattr(device, "ip_address", None),
    )
    if not ok:
        logger.warning(
            "Listener did not start for %s (port=%s, type=%s, format=%s).",
            device.device_id,
            device.port,
            getattr(device, "device_type", "picoscan"),
            getattr(device, "format_type", "compact"),
        )


def _validate_device_port(port: int, exclude_device_id: str | None = None) -> None:
    p = int(port)
    if p < PICOSCAN_PORT_MIN or p > PICOSCAN_PORT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"Port must be in range {PICOSCAN_PORT_MIN}-{PICOSCAN_PORT_MAX} for PicoScan UDP.",
        )

    for other in device_manager.get_all_devices():
        if exclude_device_id and other.device_id == exclude_device_id:
            continue
        if not bool(getattr(other, "enabled", True)):
            continue
        if int(getattr(other, "port", 0) or 0) == p:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Port {p} is already used by device {other.device_id}. "
                    "Use a unique UDP port per scanner (2115-2118)."
                ),
            )


@router.get("/", response_model=List[DeviceResponse])
async def get_all_devices():
    """Get all devices"""
    devices = device_manager.get_all_devices()
    return [device.to_dict() for device in devices]


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: str):
    """Get specific device"""
    device = device_manager.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return device.to_dict()


@router.post("/", response_model=DeviceResponse)
async def create_device(device_config: DeviceCreate, request: Request):
    """Create new device"""
    try:
        _validate_device_port(int(device_config.port))
        device = device_manager.add_device(device_config.model_dump())
        _sync_device_listener(request, device.device_id)
        return device.to_dict()
    except Exception as e:
        logger.error(f"Error creating device: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(device_id: str, device_update: DeviceUpdate, request: Request):
    """Update device"""
    device = device_manager.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    
    update_data = device_update.model_dump(exclude_unset=True)
    
    # Convert calibration back to dict if it exists
    if "calibration" in update_data and update_data["calibration"] is not None:
        cal = update_data["calibration"]
        if hasattr(cal, "model_dump"):
            update_data["calibration"] = cal.model_dump()
    
    # Add device_id to the update data
    update_data["device_id"] = device_id
    update_data["ip_address"] = update_data.get("ip_address", device.ip_address)
    update_data["port"] = update_data.get("port", device.port)
    _validate_device_port(int(update_data["port"]), exclude_device_id=device_id)
    
    if device_manager.update_device(device_id, update_data):
        _sync_device_listener(request, device_id)
        return device_manager.get_device(device_id).to_dict()
    else:
        raise HTTPException(status_code=400, detail="Failed to update device")


@router.delete("/{device_id}")
async def delete_device(device_id: str, request: Request):
    """Delete device"""
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    if receiver_manager is not None:
        try:
            receiver_manager.stop_listening(device_id)
        except Exception as exc:
            logger.warning("Failed to stop listener before delete for %s: %s", device_id, exc)
    if not device_manager.remove_device(device_id):
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return {"message": f"Device {device_id} deleted"}


@router.post("/{device_id}/connect")
async def connect_device(device_id: str):
    """Connect to device"""
    if not device_manager.connect_device(device_id):
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return {"message": f"Connected to {device_id}"}


@router.post("/{device_id}/disconnect")
async def disconnect_device(device_id: str):
    """Disconnect from device"""
    if not device_manager.disconnect_device(device_id):
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return {"message": f"Disconnected from {device_id}"}


@router.get("/{device_id}/ping")
async def ping_device(device_id: str, request: Request):
    """Check device reachability.

    reachable=True when at least one probe works:
    - ICMP ping
    - TCP connect probe (best-effort)
    - active stream health reports online
    """
    device = device_manager.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

    ip = device.ip_address
    icmp_ok = False
    tcp_ok = False
    stream_online = False

    icmp_ok = _icmp_ping(ip, timeout_ms=500)

    # Best-effort TCP probe: not every scanner exposes TCP here, so this is auxiliary only.
    try:
        with socket.create_connection((ip, int(device.port)), timeout=0.35):
            tcp_ok = True
    except Exception:
        tcp_ok = False

    # Stream health reflects actual data reception and can be online even if ICMP is blocked.
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    try:
        if receiver_manager and hasattr(receiver_manager, "get_health_snapshot"):
            health = receiver_manager.get_health_snapshot() or {}
            stream_online = str((health.get(device_id) or {}).get("availability", "")).lower() == "online"
    except Exception as exc:
        logger.debug("Health snapshot failed for %s: %s", device_id, exc)

    reachable = bool(icmp_ok or tcp_ok or stream_online)
    return {
        "device_id": device_id,
        "ip": ip,
        "reachable": reachable,
        "icmp_ok": icmp_ok,
        "tcp_ok": tcp_ok,
        "stream_online": stream_online,
    }


@router.post("/{device_id}/restart-listener")
async def restart_listener(device_id: str, request: Request):
    """Restart listener for a single device."""
    device = device_manager.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    if receiver_manager is None:
        raise HTTPException(status_code=503, detail="Receiver manager not available")

    was_registered = device_id in getattr(receiver_manager, "receivers", {})

    try:
        if was_registered:
            receiver_manager.stop_listening(device_id)
    except Exception as exc:
        logger.warning("Failed to stop listener for %s during restart: %s", device_id, exc)

    if not bool(getattr(device, "enabled", True)):
        return {
            "device_id": device_id,
            "restarted": True,
            "started": False,
            "enabled": False,
            "message": "Listener stopped. Device is disabled, so it was not started again.",
        }

    started = receiver_manager.start_listening(
        device.device_id,
        "0.0.0.0",
        device.port,
        segments_per_scan=getattr(device, "segments_per_scan", None),
        format_type=getattr(device, "format_type", "compact"),
        device_type=getattr(device, "device_type", "picoscan"),
        sensor_ip=getattr(device, "ip_address", None),
    )
    if not started:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start listener for {device_id}. Check UDP/TCP endpoint conflicts and device settings.",
        )

    return {
        "device_id": device_id,
        "restarted": True,
        "started": True,
        "enabled": True,
        "message": "Listener restarted",
    }
