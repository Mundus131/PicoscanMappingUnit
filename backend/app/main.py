from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import sys
import os
import asyncio

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from app.api.endpoints import devices, point_cloud, acquisition, calibration, tdc, system
from app.services.picoscan_receiver import PicoscanReceiverManager
from app.services.tcp_notifier import TcpNotifier
from app.services.tdc_trigger_monitor import TdcTriggerMonitor
from app.core.device_manager import device_manager

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(settings.log_file),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=settings.api_description
)

# Initialize receiver manager in app state
app.state.receiver_manager = None
app.state.acquisition_session = None
app.state.tcp_notifier = None
app.state.tdc_monitor = None
app.state.tdc_input_state = None
app.state.tdc_input_ts = None

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(devices.router, prefix="/api/v1/devices", tags=["Devices"])
app.include_router(point_cloud.router, prefix="/api/v1/point-cloud", tags=["Point Cloud"])
app.include_router(acquisition.router, prefix="/api/v1/acquisition", tags=["Real Data Acquisition"])
app.include_router(calibration.router, prefix="/api/v1/calibration", tags=["Calibration"])
app.include_router(tdc.router, prefix="/api/v1/tdc", tags=["TDC"])
app.include_router(system.router, prefix="/api/v1/system", tags=["System"])


@app.on_event("startup")
async def startup_event():
    """Auto-start UDP listening on application startup"""
    try:
        receiver_manager = PicoscanReceiverManager()
        app.state.receiver_manager = receiver_manager
        notifier = TcpNotifier(port=2120)
        notifier.start()
        app.state.tcp_notifier = notifier
        tdc_monitor = TdcTriggerMonitor(app)
        tdc_monitor.start()
        app.state.tdc_monitor = tdc_monitor
        app.state.acquisition_session = {
            "recording": False,
            "distance_mm": 0.0,
            "last_update_ts": None,
            "last_points": [],
            "accumulated_points": [],
            "last_profile_distance_mm": 0.0,
            "profiles_count": 0,
            "devices": [],
            "speed_mps": None,
            "profiling_distance_mm": None,
            "worker_thread": None,
            "worker_stop_event": None,
            "analysis_metrics": None,
            "analysis_points": [],
            "analysis_duration_ms": None,
        }
        
        # Auto-start listening for all enabled devices
        enabled_devices = [d for d in device_manager.get_all_devices() if d.enabled]
        if enabled_devices:
            for device in enabled_devices:
                # Bind on all interfaces (0.0.0.0). Binding to a specific
                # device IP (e.g. 192.168.x.x) can fail on Windows if that
                # address is not assigned to a local interface (WinError 10049).
                listen_ip = "0.0.0.0"
                # Pass per-device segments_per_scan if configured
                receiver_manager.start_listening(device.device_id, listen_ip, device.port, segments_per_scan=getattr(device, 'segments_per_scan', None))
    except Exception as e:
        logger.error(f"Error during startup: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown"""
    try:
        if app.state.receiver_manager:
            for device_id in list(app.state.receiver_manager.receivers.keys()):
                app.state.receiver_manager.stop_listening(device_id)
        if app.state.tcp_notifier:
            app.state.tcp_notifier.stop()
        if app.state.tdc_monitor:
            app.state.tdc_monitor.stop()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "version": settings.api_version}


@app.get("/status")
async def status():
    """Get application and receiver status"""
    if not app.state.receiver_manager:
        return {
            "status": "initializing",
            "receivers": {}
        }
    
    receivers_status = {}
    for device_id, receiver in app.state.receiver_manager.receivers.items():
        device = device_manager.get_device(device_id)
        receivers_status[device_id] = {
            "name": device.name if device else device_id,
            "listening": receiver["listening"] if isinstance(receiver, dict) else receiver.connected,
            "ip": receiver["listen_ip"] if isinstance(receiver, dict) else receiver.listen_ip,
            "port": receiver["listen_port"] if isinstance(receiver, dict) else receiver.listen_port,
            "segments_received": len(receiver.get("segments", [])) if isinstance(receiver, dict) else 0
        }
    
    return {
        "status": "ready",
        "receivers": receivers_status,
        "devices_total": len(device_manager.get_all_devices()),
        "devices_enabled": len([d for d in device_manager.get_all_devices() if d.enabled])
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Welcome to Picoscan Mapping Unit API",
        "version": settings.api_version,
        "docs": "/docs"
    }


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on http://{settings.server_host}:{settings.server_port}")
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False
    )
