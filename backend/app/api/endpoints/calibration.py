import logging
from fastapi import APIRouter, HTTPException, Request
from app.core.device_manager import device_manager
from app.schemas.device import (
    AutoCalibrationRequest,
    AutoCalibrationResponse,
    AutoCalibrationResult,
    AutoCalibrationApplyRequest,
    CalibrationPreviewRequest,
    PreviewFilterSettings,
    ManualCalibrationRequest,
    FrameSettings,
    MotionSettings,
    AnalysisSettings,
    OutputSettings,
    TdcSettings,
)
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


def _norm_deg(v: float) -> float:
    return ((float(v) + 180.0) % 360.0) - 180.0


def _ang_dist_deg(a: float, b: float) -> float:
    return abs(_norm_deg(float(a) - float(b)))


def _uses_lmd_stream(device) -> bool:
    fmt = str(getattr(device, "format_type", "") or "").lower()
    dtype = str(getattr(device, "device_type", "") or "").lower()
    return fmt == "lmdscandata" or dtype == "lms4000"


def _resolve_lmd_yaw(device, raw_yaw_deg: float, init_yaw_deg: float) -> float:
    """
    Resolve LMD yaw ambiguity.
    Priority:
    1) per-device configured lmd_yaw_correction_deg
    2) best candidate nearest to initial guess (90/-90/180/0)
    """
    configured = getattr(device, "lmd_yaw_correction_deg", None)
    if isinstance(configured, (int, float)):
        return _norm_deg(float(raw_yaw_deg) + float(configured))

    candidates = [90.0, -90.0, 180.0, 0.0]
    best = None
    best_err = None
    for off in candidates:
        y = _norm_deg(float(raw_yaw_deg) + off)
        err = _ang_dist_deg(y, init_yaw_deg)
        if best is None or err < float(best_err):
            best = y
            best_err = err
    return float(best if best is not None else _norm_deg(raw_yaw_deg))


def _calibration_to_matrix(calibration: dict | None) -> np.ndarray:
    cal = calibration or {}
    t = cal.get("translation") or [0.0, 0.0, 0.0]
    r = cal.get("rotation_deg") or [0.0, 0.0, 0.0]
    T = np.eye(4, dtype=float)
    T[:3, :3] = _euler_xyz_to_rotation_matrix_deg([float(r[0]), float(r[1]), float(r[2])])
    T[:3, 3] = np.array([float(t[0]), float(t[1]), float(t[2])], dtype=float)
    return T


def _initial_guess_from_device(device) -> np.ndarray:
    """
    Build ICP initial guess in data coordinates [mm].
    Prefer calibration fields (already in data coords), fallback to frame fields with conversion.
    """
    cal = getattr(device, "calibration", {}) or {}
    t = cal.get("translation")
    r = cal.get("rotation_deg")
    if not (isinstance(t, (list, tuple)) and len(t) >= 3):
        fp = getattr(device, "frame_position", None) or [0.0, 0.0, 0.0]  # [x, y, z] in meters (frame coords)
        # frame -> data mapping (inverse of _apply_calibration_to_device):
        # frame_position = [t_x/1000, t_z/1000, t_y/1000]
        t = [float(fp[0]) * 1000.0, float(fp[2]) * 1000.0, float(fp[1]) * 1000.0]
    if not (isinstance(r, (list, tuple)) and len(r) >= 3):
        fr = getattr(device, "frame_rotation_deg", None) or [0.0, 0.0, 0.0]  # yaw in index 2
        # frame yaw (Z) maps to data yaw around Y
        r = [0.0, float(fr[2]), 0.0]

    init = np.eye(4, dtype=float)
    init[:3, :3] = _euler_xyz_to_rotation_matrix_deg([float(r[0]), float(r[1]), float(r[2])])
    init[:3, 3] = np.array([float(t[0]), float(t[1]), float(t[2])], dtype=float)
    return init


def _apply_calibration_to_device(device_id: str, calibration: dict):
    device = device_manager.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    t = calibration.get("translation", [0.0, 0.0, 0.0])
    r = calibration.get("rotation_deg", [0.0, 0.0, 0.0])
    yaw = float(r[1] if len(r) > 1 else 0.0)
    frame_position = [float(t[0]) / 1000.0, float(t[2]) / 1000.0, float(t[1]) / 1000.0]
    frame_rotation_deg = [0.0, 0.0, yaw]
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


def _build_preview_response(
    *,
    request: Request,
    device_ids: list[str],
    max_points: int = 20000,
    calibration_overrides: dict | None = None,
    use_edge_filter: bool = False,
    edge_curvature_threshold: float = 0.08,
    use_voxel_denoise: bool = False,
    voxel_cell_mm: float = 8.0,
    voxel_min_points_per_cell: int = 3,
    voxel_keep_largest_component: bool = False,
    use_region_filter: bool = False,
    region_min_x_mm: float | None = None,
    region_max_x_mm: float | None = None,
    region_min_z_mm: float | None = None,
    region_max_z_mm: float | None = None,
    use_orthogonal_filter: bool = False,
    orthogonal_angle_tolerance_deg: float = 12.0,
    use_noise_filter: bool = False,
    noise_filter_k: int = 16,
    noise_filter_std_ratio: float = 1.2,
):
    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    ids = [d for d in device_ids if d]
    if not ids:
        raise HTTPException(status_code=400, detail="device_ids is required")

    if hasattr(receiver_manager, "get_point_clouds_for_devices"):
        clouds = receiver_manager.get_point_clouds_for_devices(ids, num_segments=10)
    else:
        clouds = receiver_manager.get_point_clouds(num_segments=10)
    point_clouds = []
    for device_id in ids:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        pts = clouds.get(device_id)
        if pts is None or len(pts) == 0:
            continue
        override = (calibration_overrides or {}).get(device_id)
        calibration = override or device.calibration
        point_clouds.append((pts, calibration))

    if not point_clouds:
        return {"points": [], "devices": ids, "total_points": 0}

    merged = PointCloudProcessor.merge_point_clouds(point_clouds)
    points = merged.points
    if bool((device_manager.frame_settings or {}).get("clip_points_to_frame", False)):
        points = PointCloudProcessor.clip_points_to_frame(points, device_manager.frame_settings or {})
    if bool(use_region_filter):
        if None not in (region_min_x_mm, region_max_x_mm, region_min_z_mm, region_max_z_mm):
            points = PointCloudProcessor.filter_region_xz(
                points,
                min_x_mm=float(region_min_x_mm),
                max_x_mm=float(region_max_x_mm),
                min_z_mm=float(region_min_z_mm),
                max_z_mm=float(region_max_z_mm),
            )
    if bool(use_voxel_denoise):
        points = PointCloudProcessor.filter_voxel_density(
            points,
            cell_mm=float(voxel_cell_mm),
            min_points_per_cell=int(voxel_min_points_per_cell),
            keep_largest_component=bool(voxel_keep_largest_component),
        )
    if bool(use_orthogonal_filter):
        points = PointCloudProcessor.filter_orthogonal_directions_xz(
            points,
            angle_tolerance_deg=float(orthogonal_angle_tolerance_deg),
            k=10,
        )
    if bool(use_noise_filter):
        points = PointCloudProcessor.filter_statistical_noise(
            points,
            k=int(noise_filter_k),
            std_ratio=float(noise_filter_std_ratio),
        )
    if bool(use_edge_filter):
        points = PointCloudProcessor.filter_edge_points(points, k=12, curvature_threshold=edge_curvature_threshold)

    if max_points and len(points) > max_points:
        step = max(1, len(points) // max_points)
        points = points[::step]
        if len(points) > max_points:
            points = points[:max_points]

    return {
        "points": points.tolist(),
        "devices": ids,
        "total_points": int(len(points)),
        "use_edge_filter": bool(use_edge_filter),
        "edge_curvature_threshold": float(edge_curvature_threshold),
        "use_voxel_denoise": bool(use_voxel_denoise),
        "voxel_cell_mm": float(voxel_cell_mm),
        "voxel_min_points_per_cell": int(voxel_min_points_per_cell),
        "voxel_keep_largest_component": bool(voxel_keep_largest_component),
        "use_region_filter": bool(use_region_filter),
        "region_min_x_mm": float(region_min_x_mm) if region_min_x_mm is not None else None,
        "region_max_x_mm": float(region_max_x_mm) if region_max_x_mm is not None else None,
        "region_min_z_mm": float(region_min_z_mm) if region_min_z_mm is not None else None,
        "region_max_z_mm": float(region_max_z_mm) if region_max_z_mm is not None else None,
        "use_orthogonal_filter": bool(use_orthogonal_filter),
        "orthogonal_angle_tolerance_deg": float(orthogonal_angle_tolerance_deg),
        "use_noise_filter": bool(use_noise_filter),
        "noise_filter_k": int(noise_filter_k),
        "noise_filter_std_ratio": float(noise_filter_std_ratio),
    }

@router.post("/auto", response_model=AutoCalibrationResponse)
async def auto_calibrate(req: AutoCalibrationRequest, request: Request):
    """
    Auto-calibrate devices using ICP alignment.
    Uses explicit reference_device_id when provided, otherwise first device.
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
    if hasattr(receiver_manager, "get_point_clouds_for_devices"):
        point_clouds = receiver_manager.get_point_clouds_for_devices(req.device_ids, num_segments=10)
    else:
        point_clouds = receiver_manager.get_point_clouds(num_segments=10)

    # Determine reference device
    ref_id = (req.reference_device_id or (req.device_ids[0] if req.device_ids else None))
    if not ref_id:
        raise HTTPException(status_code=400, detail="reference device is required")
    if ref_id not in req.device_ids:
        req.device_ids = [ref_id] + [d for d in req.device_ids if d != ref_id]

    # Ensure reference device has data
    ref_points = point_clouds.get(ref_id)
    if ref_points is None or len(ref_points) < 50:
        raise HTTPException(status_code=400, detail=f"No point cloud data for reference device {ref_id}")
    ref_device = device_manager.get_device(ref_id)
    if not ref_device:
        raise HTTPException(status_code=404, detail=f"Reference device {ref_id} not found")
    ref_world_T = _calibration_to_matrix(getattr(ref_device, "calibration", {}) or {})

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

        threshold = 200.0  # mm
        src_world_init = _initial_guess_from_device(device)
        criteria = o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=req.max_iterations)
        init_r = _rotation_matrix_to_euler_xyz(src_world_init[:3, :3])
        init_yaw = float(init_r[1]) if len(init_r) > 1 else 0.0
        yaw_hypotheses = [0.0, 90.0, -90.0, 180.0] if _uses_lmd_stream(device) else [0.0]

        best_reg = None
        best_offset = 0.0
        best_metric = None
        for yaw_off in yaw_hypotheses:
            src_world_guess = np.array(src_world_init, copy=True)
            guessed_r = [float(init_r[0]), _norm_deg(init_yaw + float(yaw_off)), float(init_r[2])]
            src_world_guess[:3, :3] = _euler_xyz_to_rotation_matrix_deg(guessed_r)
            # ICP expects init in target(reference-raw) coordinates:
            # source->target = inv(T_ref_world) @ T_src_world_guess
            init = np.linalg.inv(ref_world_T) @ src_world_guess
            reg_try = o3d.pipelines.registration.registration_icp(
                source,
                target,
                threshold,
                init,
                o3d.pipelines.registration.TransformationEstimationPointToPoint(),
                criteria,
            )
            fitness = float(getattr(reg_try, "fitness", 0.0) or 0.0)
            rmse = float(getattr(reg_try, "inlier_rmse", 1e9) or 1e9)
            # Primary: maximize fitness, secondary: minimize RMSE.
            metric = (fitness, -rmse)
            if best_metric is None or metric > best_metric:
                best_metric = metric
                best_reg = reg_try
                best_offset = float(yaw_off)

        if best_reg is None:
            raise HTTPException(status_code=500, detail=f"ICP failed for device {device_id}")
        reg = best_reg

        # Compose with reference calibration so results are in unified world frame.
        T = ref_world_T @ reg.transformation
        R = T[:3, :3]
        t = [float(T[0, 3]), float(T[1, 3]), float(T[2, 3])]
        r_deg = _rotation_matrix_to_euler_xyz(R)
        corrected_yaw = _norm_deg(float(r_deg[1]))
        logger.debug(
            "Auto-calibration ICP hypothesis for %s: init_yaw=%.2f chosen_offset=%.2f corrected=%.2f fitness=%.4f rmse=%.4f lmd=%s",
            device_id,
            float(init_yaw),
            float(best_offset),
            float(corrected_yaw),
            float(getattr(reg, "fitness", 0.0) or 0.0),
            float(getattr(reg, "inlier_rmse", 0.0) or 0.0),
            bool(_uses_lmd_stream(device)),
        )
        corrected_r_deg = [float(r_deg[0]), corrected_yaw, float(r_deg[2])]

        calibration = {
            "translation": t,
            "rotation_deg": corrected_r_deg,
            "scale": 1.0,
        }
        if bool(req.save_result):
            _apply_calibration_to_device(device_id, calibration)

        results.append(
            AutoCalibrationResult(
                device_id=device_id,
                translation=t,
                rotation_deg=corrected_r_deg,
                scale=1.0,
                score=float(reg.fitness) if hasattr(reg, "fitness") else None,
            )
        )

    return AutoCalibrationResponse(method=req.method, saved=bool(req.save_result), results=results)


@router.get("/preview")
async def preview_calibrated(device_ids: str, request: Request, max_points: int = 20000):
    """Return merged calibrated point cloud for preview (saved calibration only)."""
    return _build_preview_response(
        request=request,
        device_ids=[d for d in device_ids.split(",") if d],
        max_points=max_points,
    )


@router.post("/preview")
async def preview_calibrated_with_overrides(req: CalibrationPreviewRequest, request: Request):
    """Return merged calibrated point cloud for preview with optional temporary overrides."""
    overrides = None
    if req.calibration_overrides:
        overrides = {
            dev_id: {
                "translation": cal.translation,
                "rotation_deg": cal.rotation_deg,
                "scale": cal.scale,
            }
            for dev_id, cal in req.calibration_overrides.items()
        }
    return _build_preview_response(
        request=request,
        device_ids=req.device_ids,
        max_points=req.max_points,
        calibration_overrides=overrides,
        use_edge_filter=bool(req.use_edge_filter),
        edge_curvature_threshold=float(req.edge_curvature_threshold),
        use_voxel_denoise=bool(req.use_voxel_denoise),
        voxel_cell_mm=float(req.voxel_cell_mm),
        voxel_min_points_per_cell=int(req.voxel_min_points_per_cell),
        voxel_keep_largest_component=bool(req.voxel_keep_largest_component),
        use_region_filter=bool(req.use_region_filter),
        region_min_x_mm=req.region_min_x_mm,
        region_max_x_mm=req.region_max_x_mm,
        region_min_z_mm=req.region_min_z_mm,
        region_max_z_mm=req.region_max_z_mm,
        use_orthogonal_filter=bool(req.use_orthogonal_filter),
        orthogonal_angle_tolerance_deg=float(req.orthogonal_angle_tolerance_deg),
        use_noise_filter=bool(req.use_noise_filter),
        noise_filter_k=int(req.noise_filter_k),
        noise_filter_std_ratio=float(req.noise_filter_std_ratio),
    )


@router.get("/preview-filter-settings")
async def get_preview_filter_settings():
    return device_manager.preview_filter_settings


@router.put("/preview-filter-settings")
async def update_preview_filter_settings(req: PreviewFilterSettings):
    rect = list(req.region_rect_norm or [0.2, 0.15, 0.8, 0.85])
    if len(rect) != 4:
        rect = [0.2, 0.15, 0.8, 0.85]
    rect = [float(max(0.0, min(1.0, v))) for v in rect]
    device_manager.preview_filter_settings = {
        "use_edge_filter": bool(req.use_edge_filter),
        "edge_curvature_threshold": float(req.edge_curvature_threshold),
        "use_voxel_denoise": bool(req.use_voxel_denoise),
        "voxel_cell_mm": float(req.voxel_cell_mm),
        "voxel_min_points_per_cell": int(req.voxel_min_points_per_cell),
        "voxel_keep_largest_component": bool(req.voxel_keep_largest_component),
        "use_region_filter": bool(req.use_region_filter),
        "region_rect_norm": rect,
        "use_orthogonal_filter": bool(req.use_orthogonal_filter),
        "orthogonal_angle_tolerance_deg": float(req.orthogonal_angle_tolerance_deg),
        "use_noise_filter": bool(req.use_noise_filter),
        "noise_filter_k": int(req.noise_filter_k),
        "noise_filter_std_ratio": float(req.noise_filter_std_ratio),
        "visible_device_ids": list(req.visible_device_ids) if req.visible_device_ids is not None else None,
    }
    device_manager._save_to_json()
    return device_manager.preview_filter_settings


@router.post("/apply-auto-results")
async def apply_auto_calibration_results(req: AutoCalibrationApplyRequest):
    if not req.results:
        raise HTTPException(status_code=400, detail="results are required")
    applied = []
    for result in req.results:
        calibration = {
            "translation": result.translation,
            "rotation_deg": result.rotation_deg,
            "scale": result.scale,
        }
        _apply_calibration_to_device(result.device_id, calibration)
        applied.append(result.device_id)
    return {"applied": applied, "count": len(applied)}


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
        "clip_points_to_frame": bool(req.clip_points_to_frame),
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
    current = dict(device_manager.analysis_settings or {})
    active_app = str(current.get("active_app", "log") or "log").strip().lower()
    if active_app not in {"log", "none"}:
        active_app = "log"
    current["active_app"] = active_app
    return current


@router.get("/output-settings")
async def get_output_settings():
    return device_manager.output_settings


@router.put("/output-settings")
async def update_output_settings(req: OutputSettings, request: Request):
    mode = "server"
    payload_mode = (req.payload_mode or "ascii").strip().lower()
    if payload_mode not in {"ascii", "json"}:
        payload_mode = "ascii"
    selected_fields = [str(v) for v in (req.selected_fields or []) if str(v).strip()]
    if not selected_fields:
        selected_fields = [
            "timestamp_iso",
            "analysis_app",
            "volume",
            "length",
            "diameter_start",
            "diameter_end",
            "diameter_avg",
        ]
    length_unit = str(req.length_unit or "mm").strip().lower()
    if length_unit not in {"mm", "m"}:
        length_unit = "mm"
    volume_unit = str(req.volume_unit or "m3").strip().lower()
    if volume_unit not in {"m3", "l", "mm3"}:
        volume_unit = "m3"
    frame_items_raw = req.output_frame_items or []
    frame_items: list[dict] = []
    for item in frame_items_raw:
        if not isinstance(item, dict):
            continue
        itype = str(item.get("type", "field") or "field").strip().lower()
        if itype == "marker":
            marker_value = str(item.get("value", "") or item.get("text", "") or "")
            marker_label = str(item.get("label", "") or "")
            frame_items.append({"type": "marker", "value": marker_value, "label": marker_label})
            continue
        if itype == "text":
            txt = str(item.get("text", "") or "")
            frame_items.append({"type": "text", "text": txt})
            continue
        key = str(item.get("key", "") or "").strip()
        if not key:
            continue
        label = str(item.get("label", "") or "")
        try:
            precision = int(item.get("precision", req.float_precision))
        except Exception:
            precision = int(req.float_precision)
        precision = int(max(0, min(8, precision)))
        frame_items.append({"type": "field", "key": key, "label": label, "precision": precision})

    if not frame_items:
        frame_items = [{"type": "field", "key": f, "label": ""} for f in selected_fields]

    device_manager.output_settings = {
        "enabled": bool(req.enabled),
        "connection_mode": mode,
        "host": str(req.host or "0.0.0.0"),
        "port": 2120,
        "payload_mode": payload_mode,
        "separator": str(req.separator or ";"),
        "prefix": str(req.prefix or ""),
        "suffix": str(req.suffix or ""),
        "include_labels": bool(req.include_labels),
        "float_precision": int(max(0, min(8, req.float_precision))),
        "length_unit": length_unit,
        "volume_unit": volume_unit,
        "selected_fields": selected_fields,
        "output_frame_items": frame_items,
    }
    device_manager._save_to_json()
    try:
        notifier = request.app.state.tcp_notifier
    except Exception:
        notifier = None
    if notifier and hasattr(notifier, "configure"):
        notifier.configure(device_manager.output_settings)
    return device_manager.output_settings


@router.put("/analysis-settings")
async def update_analysis_settings(req: AnalysisSettings):
    current = dict(device_manager.analysis_settings or {})
    active_app = str(req.active_app or "log").strip().lower()
    if active_app not in {"log", "none"}:
        active_app = "log"
    loc_algo = (
        req.conveyor_localization_algorithm
        if req.conveyor_localization_algorithm is not None
        else current.get("conveyor_localization_algorithm", "object_cloud_bbox")
    )
    if loc_algo not in {"object_cloud_bbox", "box_top_plane"}:
        loc_algo = "object_cloud_bbox"
    device_manager.analysis_settings = {
        "active_app": active_app,
        "log_window_profiles": int(req.log_window_profiles if req.log_window_profiles is not None else current.get("log_window_profiles", 10)),
        "log_min_points": int(req.log_min_points if req.log_min_points is not None else current.get("log_min_points", 50)),
        "conveyor_localization_algorithm": loc_algo,
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
        "conveyor_top_plane_quantile": float(
            req.conveyor_top_plane_quantile
            if req.conveyor_top_plane_quantile is not None
            else current.get("conveyor_top_plane_quantile", 0.88)
        ),
        "conveyor_top_plane_inlier_mm": float(
            req.conveyor_top_plane_inlier_mm
            if req.conveyor_top_plane_inlier_mm is not None
            else current.get("conveyor_top_plane_inlier_mm", 4.0)
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
