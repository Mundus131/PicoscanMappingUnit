import os
import platform
import threading
import time
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter()


@router.post("/restart")
async def restart_backend():
    """Restart the backend process. Requires a process manager / uvicorn reload."""
    def _exit_later():
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=_exit_later, daemon=True).start()
    return {"status": "restarting"}


@router.get("/metrics")
async def system_metrics() -> Dict[str, Any]:
    """Return basic system metrics for dashboard monitoring."""
    try:
        import psutil
    except Exception as exc:  # pragma: no cover
        return {
            "available": False,
            "error": f"psutil not available: {exc}",
        }

    os_name = platform.system()
    drive_root = os.getenv("SystemDrive", "C:") + "\\" if os_name.lower() == "windows" else "/"
    boot_ts = psutil.boot_time()
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(drive_root)
    proc = psutil.Process(os.getpid())

    return {
        "available": True,
        "os": {
            "name": os_name,
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "cpu": {
            "percent": psutil.cpu_percent(interval=None),
            "cores_logical": psutil.cpu_count(logical=True),
            "cores_physical": psutil.cpu_count(logical=False),
        },
        "memory": {
            "total_bytes": vm.total,
            "available_bytes": vm.available,
            "used_bytes": vm.used,
            "percent": vm.percent,
        },
        "disk": {
            "path": drive_root,
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "percent": disk.percent,
        },
        "uptime_s": max(0.0, time.time() - boot_ts),
        "process": {
            "pid": proc.pid,
            "rss_bytes": proc.memory_info().rss,
            "cpu_percent": proc.cpu_percent(interval=None),
        },
        "timestamp": time.time(),
    }
