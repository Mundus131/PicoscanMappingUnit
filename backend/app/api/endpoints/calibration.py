import logging
from fastapi import APIRouter, HTTPException, Request
from app.core.device_manager import device_manager
from app.schemas.device import AutoCalibrationRequest, AutoCalibrationResponse, AutoCalibrationResult, ManualCalibrationRequest, FrameSettings, MotionSettings, AnalysisSettings, TdcSettings
import numpy as np
from app.services.point_cloud_processor import PointCloudProcessor

logger = logging.getLogger(__name__)
router = APIRouter()


def _rotation_matrix_to_euler_xyz(R: np.ndarray) -> list[float]:
    sy = np.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])
    singular = sy < 1e-6
    if not singular:
        x = np.arctan2(R[2, 1], R[2, 2])
        y = np.arctan2(-R[2, 0], sy)
        z = np.arctan2(R[1, 0], R[0, 0])
    else:
        x = np.arctan2(-R[1, 2], R[1, 1])
        y = np.arctan2(-R[2, 0], sy)
        z = 0
    return [float(np.degrees(x)), float(np.degrees(y)), float(np.degrees(z))]

def _euler_xyz_to_rotation_matrix_deg(r_deg: list[float]) -> np.ndarray:
    rx, ry, rz = [np.radians(v) for v in r_deg]
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx

@router.post("/auto", response_model=AutoCalibrationResponse)
async def auto_calibrate(req: AutoCalibrationRequest, request: Request):
    """
    Auto-calibrate devices using ICP alignment.
    Uses first device as reference frame.
    """
    try:
        import open3d as o3d
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Open3D not installed: {e}")

    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    if not req.device_ids or len(req.device_ids) < 1:
        raise HTTPException(status_code=400, detail="device_ids is required")

    # Acquire point clouds from receivers
    point_clouds = receiver_manager.get_point_clouds(num_segments=10)

    # Ensure reference device has data
    ref_id = req.device_ids[0]
    ref_points = point_clouds.get(ref_id)
    if ref_points is None or len(ref_points) < 50:
        raise HTTPException(status_code=400, detail=f"No point cloud data for reference device {ref_id}")

    def to_o3d(points: np.ndarray) -> o3d.geometry.PointCloud:
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(points[:, :3].astype(np.float64))
        return pcd

    target = to_o3d(ref_points)

    results = []
    for device_id in req.device_ids:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

        if device_id == ref_id:
            cal = device.calibration or {"translation": [0.0, 0.0, 0.0], "rotation_deg": [0.0, 0.0, 0.0], "scale": 1.0}
            results.append(
                AutoCalibrationResult(
                    device_id=device_id,
                    translation=cal.get("translation", [0.0, 0.0, 0.0]),
                    rotation_deg=cal.get("rotation_deg", [0.0, 0.0, 0.0]),
                    scale=cal.get("scale", 1.0),
                    score=None,
                )
            )
            continue

        source_points = point_clouds.get(device_id)
        if source_points is None or len(source_points) < 50:
            raise HTTPException(status_code=400, detail=f"No point cloud data for device {device_id}")

        source = to_o3d(source_points)

        # ICP registration with initial guess from frame placement
        threshold = 200.0  # mm
        init = np.eye(4)
        init_t = device.frame_position or device.calibration.get("translation") or [0.0, 0.0, 0.0]
        init_r = device.frame_rotation_deg or device.calibration.get("rotation_deg") or [0.0, 0.0, 0.0]
        init[:3, :3] = _euler_xyz_to_rotation_matrix_deg(init_r)
        init[:3, 3] = np.array(init_t, dtype=float)
        criteria = o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=req.max_iterations)
        reg = o3d.pipelines.registration.registration_icp(
            source,
            target,
            threshold,
            init,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(),
            criteria,
        )

        T = reg.transformation
        R = T[:3, :3]
        t = T[:3, 3].tolist()
        r_deg = _rotation_matrix_to_euler_xyz(R)

        calibration = {
            "translation": t,
            "rotation_deg": r_deg,
            "scale": 1.0,
        }
        # Map data coords -> frame coords (frame X/Y plane, Z motion)
        frame_position = [t[0] / 1000.0, t[2] / 1000.0, t[1] / 1000.0]
        frame_rotation_deg = [0.0, 0.0, float(r_deg[1])]
        device_manager.update_device(
            device_id,
            {
                "device_id": device_id,
                "ip_address": device.ip_address,
                "port": device.port,
                "calibration": calibration,
                "frame_position": frame_position,
                "frame_rotation_deg": frame_rotation_deg,
            },
        )

        results.append(
            AutoCalibrationResult(
                device_id=device_id,
                translation=t,
                rotation_deg=r_deg,
                scale=1.0,
                score=float(reg.fitness) if hasattr(reg, "fitness") else None,
            )
        )

    return AutoCalibrationResponse(method=req.method, results=results)


@router.get("/preview")
async def preview_calibrated(device_ids: str, request: Request, max_points: int = 20000):
    """Return merged calibrated point cloud for preview."""
    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    ids = [d for d in device_ids.split(',') if d]
    if not ids:
        raise HTTPException(status_code=400, detail="device_ids is required")

    clouds = receiver_manager.get_point_clouds(num_segments=10)
    point_clouds = []
    for device_id in ids:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        pts = clouds.get(device_id)
        if pts is None or len(pts) == 0:
            continue
        point_clouds.append((pts, device.calibration))

    if not point_clouds:
        return {"points": [], "devices": ids, "total_points": 0}

    merged = PointCloudProcessor.merge_point_clouds(point_clouds)
    points = merged.points

    if max_points and len(points) > max_points:
        idx = np.random.choice(len(points), max_points, replace=False)
        points = points[idx]

    return {
        "points": points.tolist(),
        "devices": ids,
        "total_points": int(len(points)),
    }


@router.post("/manual")
async def manual_calibrate(req: ManualCalibrationRequest):
    """Apply manual calibration for a device."""
    device = device_manager.get_device(req.device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {req.device_id} not found")

    calibration = {
        "translation": req.translation,
        "rotation_deg": req.rotation_deg,
        "scale": req.scale,
    }
    if not device_manager.update_device_calibration(req.device_id, calibration):
        raise HTTPException(status_code=400, detail="Failed to update calibration")

    return {"message": "Calibration updated", "device_id": req.device_id, "calibration": calibration}


@router.get("/frame-settings")
async def get_frame_settings():
    return device_manager.frame_settings


@router.put("/frame-settings")
async def update_frame_settings(req: FrameSettings):
    device_manager.frame_settings = {
        "width_m": req.width_m,
        "height_m": req.height_m,
        "origin_mode": req.origin_mode,
    }
    device_manager._save_to_json()
    return device_manager.frame_settings


@router.get("/motion-settings")
async def get_motion_settings():
    return device_manager.motion_settings


@router.put("/motion-settings")
async def update_motion_settings(req: MotionSettings):
    device_manager.motion_settings = {
        "mode": req.mode,
        "fixed_speed_mps": req.fixed_speed_mps,
        "profiling_distance_mm": req.profiling_distance_mm,
        "encoder_wheel_mode": req.encoder_wheel_mode or "diameter",
        "encoder_wheel_value_mm": req.encoder_wheel_value_mm or 0.0,
        "encoder_rps": req.encoder_rps or 0.0,
    }
    device_manager._save_to_json()
    return device_manager.motion_settings


@router.get("/tdc-settings")
async def get_tdc_settings():
    return device_manager.tdc_settings


@router.get("/analysis-settings")
async def get_analysis_settings():
    return device_manager.analysis_settings


@router.put("/analysis-settings")
async def update_analysis_settings(req: AnalysisSettings):
    current = dict(device_manager.analysis_settings or {})
    active_app = (req.active_app or "log").strip()
    if active_app not in {"log", "conveyor_object"}:
        active_app = "log"
    device_manager.analysis_settings = {
        "active_app": active_app,
        "log_window_profiles": int(req.log_window_profiles if req.log_window_profiles is not None else current.get("log_window_profiles", 10)),
        "log_min_points": int(req.log_min_points if req.log_min_points is not None else current.get("log_min_points", 50)),
        "conveyor_plane_quantile": float(
            req.conveyor_plane_quantile
            if req.conveyor_plane_quantile is not None
            else current.get("conveyor_plane_quantile", 0.35)
        ),
        "conveyor_plane_inlier_mm": float(
            req.conveyor_plane_inlier_mm
            if req.conveyor_plane_inlier_mm is not None
            else current.get("conveyor_plane_inlier_mm", 8.0)
        ),
        "conveyor_object_min_height_mm": float(
            req.conveyor_object_min_height_mm
            if req.conveyor_object_min_height_mm is not None
            else current.get("conveyor_object_min_height_mm", 8.0)
        ),
        "conveyor_object_max_points": int(
            req.conveyor_object_max_points
            if req.conveyor_object_max_points is not None
            else current.get("conveyor_object_max_points", 60000)
        ),
        "conveyor_denoise_enabled": bool(
            req.conveyor_denoise_enabled
            if req.conveyor_denoise_enabled is not None
            else current.get("conveyor_denoise_enabled", True)
        ),
        "conveyor_denoise_cell_mm": float(
            req.conveyor_denoise_cell_mm
            if req.conveyor_denoise_cell_mm is not None
            else current.get("conveyor_denoise_cell_mm", 8.0)
        ),
        "conveyor_denoise_min_points_per_cell": int(
            req.conveyor_denoise_min_points_per_cell
            if req.conveyor_denoise_min_points_per_cell is not None
            else current.get("conveyor_denoise_min_points_per_cell", 3)
        ),
        "conveyor_keep_largest_component": bool(
            req.conveyor_keep_largest_component
            if req.conveyor_keep_largest_component is not None
            else current.get("conveyor_keep_largest_component", True)
        ),
    }
    device_manager._save_to_json()
    return device_manager.analysis_settings


@router.put("/tdc-settings")
async def update_tdc_settings(req: TdcSettings):
    device_manager.tdc_settings = {
        "enabled": req.enabled,
        "ip_address": req.ip_address,
        "port": req.port,
        "login": req.login,
        "password": req.password,
        "realm": req.realm or "admin",
        "trigger_input": req.trigger_input,
        "poll_interval_ms": req.poll_interval_ms,
        "token_refresh_interval_s": req.token_refresh_interval_s or 300,
        "grpc_timeout_s": req.grpc_timeout_s or 5.0,
        "encoder_port": req.encoder_port or "1",
        "start_delay_mode": req.start_delay_mode or "time",
        "start_delay_ms": req.start_delay_ms or 0.0,
        "start_delay_mm": req.start_delay_mm or 0.0,
        "stop_delay_mode": req.stop_delay_mode or "time",
        "stop_delay_ms": req.stop_delay_ms or 0.0,
        "stop_delay_mm": req.stop_delay_mm or 0.0,
    }
    device_manager._save_to_json()
    return device_manager.tdc_settings
