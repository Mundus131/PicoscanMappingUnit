import os
import threading
import time

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
