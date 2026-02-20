from fastapi import APIRouter, HTTPException, Request
from typing import List
from app.core.device_manager import device_manager
from app.schemas.device import DeviceResponse, DeviceCreate, DeviceUpdate
import logging
import subprocess

logger = logging.getLogger(__name__)
router = APIRouter()

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
    
    if device_manager.update_device(device_id, update_data):
        _sync_device_listener(request, device_id)
        return device_manager.get_device(device_id).to_dict()
    else:
        raise HTTPException(status_code=400, detail="Failed to update device")


@router.delete("/{device_id}")
async def delete_device(device_id: str):
    """Delete device"""
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
async def ping_device(device_id: str):
    """Ping device IP to check connectivity."""
    device = device_manager.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

    ip = device.ip_address
    try:
        # Windows ping: -n 1 (one packet), -w 500 (timeout ms)
        result = subprocess.run(["ping", "-n", "1", "-w", "500", ip], capture_output=True, text=True)
        ok = result.returncode == 0
    except Exception as e:
        logger.error(f"Ping error for {ip}: {e}")
        ok = False

    return {"device_id": device_id, "ip": ip, "reachable": ok}
