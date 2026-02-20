from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import List, Generator
from pydantic import BaseModel
from app.core.device_manager import device_manager
from app.analysis.log_measurement import compute_log_metrics, build_augmented_cloud
from app.analysis.conveyor_measurement import compute_conveyor_object_metrics, build_conveyor_augmented_cloud
from app.analysis.history_store import save_measurement, list_measurements, get_measurement
from app.services.picoscan_receiver import PicoscanReceiver, PicoscanReceiverManager
from app.services.lms4000_receiver import Lms4000Receiver
from app.services.point_cloud_processor import PointCloudProcessor, PointCloud
from app.services import tdc_rest
import logging
import json
import time
import asyncio
import numpy as np
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
router = APIRouter()


class AcquisitionRequest(BaseModel):
    device_ids: List[str]
    num_segments: int = 1


def _region_bounds_from_normalized_rect(frame_settings: dict, rect_norm: list[float]) -> tuple[float, float, float, float] | None:
    if not isinstance(rect_norm, list) or len(rect_norm) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(v) for v in rect_norm]
    except Exception:
        return None
    x0 = max(0.0, min(1.0, x0))
    x1 = max(0.0, min(1.0, x1))
    y0 = max(0.0, min(1.0, y0))
    y1 = max(0.0, min(1.0, y1))
    nx0, nx1 = min(x0, x1), max(x0, x1)
    ny0, ny1 = min(y0, y1), max(y0, y1)

    width_m = float((frame_settings or {}).get("width_m", 0.0) or 0.0)
    height_m = float((frame_settings or {}).get("height_m", 0.0) or 0.0)
    origin_mode = str((frame_settings or {}).get("origin_mode", "center") or "center")
    if width_m <= 0 or height_m <= 0:
        return None
    width_mm = width_m * 1000.0
    height_mm = height_m * 1000.0
    if origin_mode == "center":
        x_min = -width_mm / 2.0
        x_max = width_mm / 2.0
        z_min = -height_mm / 2.0
        z_max = height_mm / 2.0
    else:
        x_min = 0.0
        x_max = width_mm
        z_min = 0.0
        z_max = height_mm
    span_x = x_max - x_min
    span_z = z_max - z_min
    rx0 = x_min + nx0 * span_x
    rx1 = x_min + nx1 * span_x
    rz0 = z_max - ny1 * span_z
    rz1 = z_max - ny0 * span_z
    return (rx0, rx1, rz0, rz1)


def _apply_configured_preview_filters(points: np.ndarray) -> np.ndarray:
    if points is None or len(points) == 0:
        return points
    frame = device_manager.frame_settings or {}
    cfg = device_manager.preview_filter_settings or {}

    if bool(frame.get("clip_points_to_frame", False)):
        points = PointCloudProcessor.clip_points_to_frame(points, frame)

    if bool(cfg.get("use_region_filter", False)):
        bounds = _region_bounds_from_normalized_rect(frame, cfg.get("region_rect_norm") or [0.2, 0.15, 0.8, 0.85])
        if bounds is not None:
            points = PointCloudProcessor.filter_region_xz(points, bounds[0], bounds[1], bounds[2], bounds[3])

    if bool(cfg.get("use_voxel_denoise", False)):
        points = PointCloudProcessor.filter_voxel_density(
            points,
            cell_mm=float(cfg.get("voxel_cell_mm", 8.0) or 8.0),
            min_points_per_cell=int(cfg.get("voxel_min_points_per_cell", 3) or 3),
            keep_largest_component=bool(cfg.get("voxel_keep_largest_component", False)),
        )

    if bool(cfg.get("use_orthogonal_filter", False)):
        points = PointCloudProcessor.filter_orthogonal_directions_xz(
            points,
            angle_tolerance_deg=float(cfg.get("orthogonal_angle_tolerance_deg", 12.0) or 12.0),
            k=10,
        )

    if bool(cfg.get("use_noise_filter", False)):
        points = PointCloudProcessor.filter_statistical_noise(
            points,
            k=int(cfg.get("noise_filter_k", 16) or 16),
            std_ratio=float(cfg.get("noise_filter_std_ratio", 1.2) or 1.2),
        )

    if bool(cfg.get("use_edge_filter", False)):
        points = PointCloudProcessor.filter_edge_points(
            points,
            k=12,
            curvature_threshold=float(cfg.get("edge_curvature_threshold", 0.08) or 0.08),
        )

    return points


def _get_session_from_app(app) -> dict:
    session = getattr(app.state, "acquisition_session", None)
    if session is None:
        session = {
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
            "analysis_timestamp_ms": None,
            "start_delay_mm_remaining": 0.0,
            "trigger_source": "manual",
        }
        app.state.acquisition_session = session
    # Ensure keys exist also for sessions initialized in app startup
    session.setdefault("encoder_thread", None)
    session.setdefault("encoder_stop_event", None)
    session.setdefault("encoder_rpm", None)
    session.setdefault("encoder_speed_mps", None)
    session.setdefault("encoder_last_data", None)
    session.setdefault("analysis_timestamp_ms", None)
    return session


def _get_session(request: Request) -> dict:
    return _get_session_from_app(request.app)


def _calc_speed_from_encoder_rpm(rpm: float, motion: dict) -> float | None:
    encoder_mode = motion.get("encoder_wheel_mode", "diameter") or "diameter"
    encoder_value_mm = float(motion.get("encoder_wheel_value_mm", 0.0) or 0.0)
    if encoder_value_mm <= 0:
        return None
    if encoder_mode == "circumference":
        circ_m = encoder_value_mm / 1000.0
    else:
        circ_m = (encoder_value_mm * np.pi) / 1000.0
    # TDC velocity is RPM (rotations per minute)
    rps = abs(float(rpm)) / 60.0
    return rps * circ_m


def _extract_encoder_rpm(payload: dict) -> float | None:
    try:
        raw = (
            payload.get("data", {})
            .get("getData", {})
            .get("iolink", {})
            .get("value", {})
            .get("Velocity", {})
            .get("value")
        )
        if raw is None:
            return None
        return float(raw)
    except Exception:
        return None


def _run_encoder_loop(session: dict):
    stop_event = session.get("encoder_stop_event")
    while stop_event and not stop_event.is_set():
        motion = device_manager.motion_settings or {}
        if (motion.get("mode", "fixed") or "fixed") != "encoder":
            session["encoder_rpm"] = None
            session["encoder_speed_mps"] = None
            time.sleep(0.2)
            continue

        tdc_cfg = device_manager.tdc_settings or {}
        poll_interval_ms = max(50, int(tdc_cfg.get("poll_interval_ms", 200) or 200))
        if not bool(tdc_cfg.get("enabled", False)):
            session["encoder_rpm"] = None
            session["encoder_speed_mps"] = None
            time.sleep(poll_interval_ms / 1000.0)
            continue

        try:
            payload = tdc_rest.fetch_encoder_process_data(data_format="iodd")
            rpm = _extract_encoder_rpm(payload)
            speed_mps = _calc_speed_from_encoder_rpm(rpm, motion) if rpm is not None else None
            session["encoder_last_data"] = payload
            session["encoder_rpm"] = rpm
            session["encoder_speed_mps"] = speed_mps
            # Keep live speed visible even when trigger recording is stopped.
            session["speed_mps"] = speed_mps
        except Exception as exc:
            logger.debug("Encoder poll failed: %s", exc)
            session["encoder_rpm"] = None
            session["encoder_speed_mps"] = None

        time.sleep(poll_interval_ms / 1000.0)


def _sync_encoder_worker(session: dict):
    motion = device_manager.motion_settings or {}
    mode = motion.get("mode", "fixed") or "fixed"
    should_run = mode == "encoder"
    thread = session.get("encoder_thread")
    running = bool(thread and thread.is_alive())

    if should_run and not running:
        stop_event = threading.Event()
        session["encoder_stop_event"] = stop_event
        worker = threading.Thread(target=_run_encoder_loop, args=(session,), daemon=True)
        session["encoder_thread"] = worker
        worker.start()
    elif (not should_run) and running:
        stop_event = session.get("encoder_stop_event")
        if stop_event:
            stop_event.set()
        session["encoder_thread"] = None
        session["encoder_stop_event"] = None
        session["encoder_rpm"] = None
        session["encoder_speed_mps"] = None


def ensure_encoder_monitor_started(app):
    session = _get_session_from_app(app)
    _sync_encoder_worker(session)


def stop_encoder_monitor(app):
    session = _get_session_from_app(app)
    stop_event = session.get("encoder_stop_event")
    if stop_event:
        stop_event.set()
    session["encoder_thread"] = None
    session["encoder_stop_event"] = None
    session["encoder_rpm"] = None
    session["encoder_speed_mps"] = None


def _update_session_once(session: dict, receiver_manager):
    if receiver_manager is None:
        return

    _sync_encoder_worker(session)

    # update distance based on motion settings
    motion = device_manager.motion_settings or {}
    mode = motion.get("mode", "fixed")
    fixed_speed = motion.get("fixed_speed_mps", 0.0) or 0.0
    encoder_rps = motion.get("encoder_rps", 0.0) or 0.0
    encoder_mode = motion.get("encoder_wheel_mode", "diameter") or "diameter"
    encoder_value_mm = motion.get("encoder_wheel_value_mm", 0.0) or 0.0
    profiling_distance_mm = motion.get("profiling_distance_mm", None)
    if profiling_distance_mm is None:
        profiling_distance_mm = 10.0

    now = time.time()
    last_ts = session.get("last_update_ts")
    current_speed_mps = None
    if mode == "fixed":
        current_speed_mps = fixed_speed
    elif mode == "encoder":
        encoder_live_speed = session.get("encoder_speed_mps")
        if encoder_live_speed is not None:
            current_speed_mps = float(encoder_live_speed)
        elif encoder_value_mm > 0 and encoder_rps > 0:
            if encoder_mode == "circumference":
                circ_m = encoder_value_mm / 1000.0
            else:
                circ_m = (encoder_value_mm * np.pi) / 1000.0
            current_speed_mps = encoder_rps * circ_m

    if last_ts is not None:
        dt = max(0.0, now - last_ts)
        if current_speed_mps is not None:
            session["distance_mm"] = float(session.get("distance_mm", 0.0) + current_speed_mps * dt * 1000.0)
    session["last_update_ts"] = now
    session["speed_mps"] = current_speed_mps
    session["profiling_distance_mm"] = profiling_distance_mm

    # fetch point clouds and merge
    try:
        point_clouds = receiver_manager.get_point_clouds(num_segments=10)
        point_clouds_to_merge = []
        for device_id, points in point_clouds.items():
            device = device_manager.get_device(device_id)
            if not device:
                continue
            if points is None or len(points) == 0:
                continue
            point_clouds_to_merge.append((points, device.calibration))

        if point_clouds_to_merge:
            merged = PointCloudProcessor.merge_point_clouds(point_clouds_to_merge)
            pts = merged.points
            if getattr(merged, "intensities", None) is not None:
                try:
                    if len(merged.intensities) == len(merged.points):
                        pts = np.column_stack([merged.points, merged.intensities])
                except Exception:
                    pts = merged.points
            pts = _apply_configured_preview_filters(pts)
            # Normalize RSSI to 0-100 if present
            if isinstance(pts, np.ndarray) and pts.shape[1] >= 4:
                try:
                    rssi = pts[:, 3].astype(np.float32)
                    rssi_min = np.nanmin(rssi)
                    rssi_max = np.nanmax(rssi)
                    if np.isfinite(rssi_min) and np.isfinite(rssi_max) and rssi_max > rssi_min:
                        pts[:, 3] = (rssi - rssi_min) / (rssi_max - rssi_min) * 100.0
                    else:
                        pts[:, 3] = 0.0
                except Exception:
                    pass
            # limit size for transport
            max_points = 20000
            if len(pts) > max_points:
                step = max(1, len(pts) // max_points)
                pts = pts[::step]
                if len(pts) > max_points:
                    pts = pts[:max_points]
            session["last_points"] = pts.tolist()

            # Build 3D stack along Z based on traveled distance
            if session.get("recording"):
                last_profile_distance = float(session.get("last_profile_distance_mm", 0.0))
                distance_mm = float(session.get("distance_mm", 0.0))
                delta = distance_mm - last_profile_distance
                # apply distance-based start delay by skipping accumulation
                delay_remaining = float(session.get("start_delay_mm_remaining", 0.0))
                if delay_remaining > 0:
                    if delta > 0:
                        remaining = max(0.0, delay_remaining - delta)
                        session["start_delay_mm_remaining"] = remaining
                    return
                if profiling_distance_mm > 0 and delta >= profiling_distance_mm:
                    profiles_to_add = int(delta // profiling_distance_mm)
                    profile_points = np.asarray(pts, dtype=np.float32)
                    accumulated = session.get("accumulated_points") or []
                    if profiles_to_add == 1:
                        step_distance = last_profile_distance + profiling_distance_mm
                        if profile_points.shape[1] >= 3:
                            prof = profile_points.copy()
                            prof[:, 1] = step_distance
                        else:
                            prof = profile_points
                        accumulated.extend(prof.tolist())
                    else:
                        for i in range(profiles_to_add):
                            step_distance = last_profile_distance + profiling_distance_mm * (i + 1)
                            if profile_points.shape[1] >= 3:
                                prof = profile_points.copy()
                                # Use data Y as motion axis (matches x/z scan plane in UI)
                                prof[:, 1] = step_distance
                            else:
                                prof = profile_points
                            accumulated.extend(prof.tolist())
                    session["profiles_count"] = int(session.get("profiles_count", 0) + profiles_to_add)
                    # Cap accumulated size to avoid memory blow-up
                    max_accum = 200000
                    if len(accumulated) > max_accum:
                        accumulated = accumulated[-max_accum:]
                    session["accumulated_points"] = accumulated
                    session["last_profile_distance_mm"] = last_profile_distance + profiling_distance_mm * profiles_to_add
    except Exception:
        pass


def _run_acquisition_loop(app):
    session = app.state.acquisition_session
    stop_event = session.get("worker_stop_event")
    receiver_manager = app.state.receiver_manager
    while stop_event and not stop_event.is_set():
        if session.get("recording"):
            _update_session_once(session, receiver_manager)
        time.sleep(0.05)


def start_trigger_session(app):
    """Start acquisition session (profile recording) for internal calls."""
    session = _get_session_from_app(app)
    if session.get("recording"):
        return session

    if session.get("worker_thread") is None or not session["worker_thread"].is_alive():
        stop_event = threading.Event()
        session["worker_stop_event"] = stop_event
        worker = threading.Thread(target=_run_acquisition_loop, args=(app,), daemon=True)
        session["worker_thread"] = worker
        worker.start()

    session["recording"] = True
    session["distance_mm"] = 0.0
    session["last_update_ts"] = time.time()
    session["accumulated_points"] = []
    session["last_profile_distance_mm"] = 0.0
    session["profiles_count"] = 0
    session["start_delay_mm_remaining"] = 0.0
    session["devices"] = [d.device_id for d in device_manager.get_all_devices() if d.enabled]
    _sync_encoder_worker(session)
    _update_session_once(session, app.state.receiver_manager)
    return session


def _run_analysis_for_session(session: dict, app):
    try:
        t0 = time.time()
        points = session.get("accumulated_points") or []
        profiling_distance_mm = session.get("profiling_distance_mm") or 10.0
        analysis_cfg = device_manager.analysis_settings or {}
        analysis_app = str(analysis_cfg.get("active_app", "log") or "log")
        session["analysis_timestamp_ms"] = int(time.time() * 1000)
        session["analysis_app"] = analysis_app
        if points:
            metrics_for_save = None
            if analysis_app == "conveyor_object":
                metrics = compute_conveyor_object_metrics(
                    points,
                    plane_quantile=float(analysis_cfg.get("conveyor_plane_quantile", 0.35) or 0.35),
                    plane_inlier_mm=float(analysis_cfg.get("conveyor_plane_inlier_mm", 8.0) or 8.0),
                    object_min_height_mm=float(analysis_cfg.get("conveyor_object_min_height_mm", 8.0) or 8.0),
                    localization_algorithm=str(analysis_cfg.get("conveyor_localization_algorithm", "object_cloud_bbox") or "object_cloud_bbox"),
                    top_plane_quantile=float(analysis_cfg.get("conveyor_top_plane_quantile", 0.88) or 0.88),
                    top_plane_inlier_mm=float(analysis_cfg.get("conveyor_top_plane_inlier_mm", 4.0) or 4.0),
                    denoise_enabled=bool(analysis_cfg.get("conveyor_denoise_enabled", True)),
                    denoise_cell_mm=float(analysis_cfg.get("conveyor_denoise_cell_mm", 8.0) or 8.0),
                    denoise_min_points_per_cell=int(analysis_cfg.get("conveyor_denoise_min_points_per_cell", 3) or 3),
                    keep_largest_component=bool(analysis_cfg.get("conveyor_keep_largest_component", True)),
                )
                # Remove internal arrays from persisted/public metrics
                public_metrics = {k: v for k, v in metrics.items() if k != "_internal"}
                session["analysis_metrics"] = public_metrics
                metrics_for_save = public_metrics
                session["analysis_points"] = build_conveyor_augmented_cloud(
                    points,
                    metrics,
                    max_points=int(analysis_cfg.get("conveyor_object_max_points", 60000) or 60000),
                )
            else:
                y_vals = [p[1] for p in points if len(p) >= 2]
                y_min = min(y_vals) if y_vals else None
                y_max = max(y_vals) if y_vals else None
                metrics = compute_log_metrics(
                    points,
                    profiling_distance_mm=profiling_distance_mm,
                    window_profiles=int(analysis_cfg.get("log_window_profiles", 10) or 10),
                    min_points=int(analysis_cfg.get("log_min_points", 50) or 50),
                    y_min=y_min,
                    y_max=y_max,
                )
                session["analysis_metrics"] = metrics
                metrics_for_save = metrics
                session["analysis_points"] = build_augmented_cloud(
                    metrics,
                    points_per_circle=180,
                    rssi_value=80.0,
                    profiling_distance_mm=profiling_distance_mm,
                    y_min=y_min,
                    y_max=y_max,
                )
            session["analysis_duration_ms"] = int((time.time() - t0) * 1000)
            try:
                notifier = app.state.tcp_notifier
            except Exception:
                notifier = None
            output_cfg = dict(device_manager.output_settings or {})
            if notifier and bool(output_cfg.get("enabled", False)):
                values = _flatten_output_values(session, analysis_app, metrics_for_save or session.get("analysis_metrics"))
                values = _apply_output_units(values, output_cfg)
                payload = _format_output_payload(values, output_cfg)
                notifier.broadcast(payload)
            try:
                save_measurement({
                    "analysis_app": analysis_app,
                    "metrics": metrics_for_save or session.get("analysis_metrics"),
                    "original_points": points,
                    "augmented_points": session["analysis_points"],
                    "analysis_timestamp_ms": session.get("analysis_timestamp_ms"),
                    "profiling_distance_mm": profiling_distance_mm,
                    "profiles_count": session.get("profiles_count", 0),
                    "distance_mm": session.get("distance_mm", 0.0),
                    "devices": session.get("devices", []),
                    "analysis_duration_ms": session.get("analysis_duration_ms"),
                })
            except Exception:
                pass
        else:
            session["analysis_metrics"] = None
            session["analysis_points"] = []
            session["analysis_duration_ms"] = None
            session["analysis_timestamp_ms"] = None
    except Exception:
        session["analysis_metrics"] = None
        session["analysis_points"] = []
        session["analysis_duration_ms"] = None
        session["analysis_timestamp_ms"] = None


def stop_trigger_session(app):
    """Stop acquisition session for internal calls."""
    session = _get_session_from_app(app)
    if not session.get("recording"):
        return session
    session["recording"] = False
    _sync_encoder_worker(session)
    stop_event = session.get("worker_stop_event")
    if stop_event:
        stop_event.set()
    _run_analysis_for_session(session, app)
    return session


def _extract_segments(result):
    """Normalize scansegmentapi result to a list of segment dicts."""
    if not result:
        return []
    if isinstance(result, tuple):
        segments = result[0]
        return segments if isinstance(segments, list) else [segments]
    return result if isinstance(result, list) else [result]


def _flatten_output_values(session: dict, analysis_app: str, metrics: dict | None) -> dict:
    ts_ms = int(session.get("analysis_timestamp_ms") or int(time.time() * 1000))
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    out = {
        "timestamp_ms": ts_ms,
        "timestamp_iso": dt.isoformat(),
        "analysis_app": analysis_app,
        "distance_mm": float(session.get("distance_mm", 0.0) or 0.0),
        "profiles_count": int(session.get("profiles_count", 0) or 0),
    }
    m = metrics or {}
    if analysis_app == "log":
        slices = m.get("slices") or []
        first = slices[0] if slices else {}
        last = slices[-1] if slices else {}
        diam = m.get("diameter_mm") or {}
        length_mm = m.get("total_length_mm")
        if length_mm is None and m.get("y_min_mm") is not None and m.get("y_max_mm") is not None:
            length_mm = float(m.get("y_max_mm")) - float(m.get("y_min_mm"))
        out.update(
            {
                "volume_m3": m.get("volume_m3"),
                "volume_mm3": m.get("volume_mm3"),
                "length_mm": length_mm,
                "diameter_start_mm": first.get("diameter_mm"),
                "diameter_end_mm": last.get("diameter_mm"),
                "diameter_avg_mm": diam.get("avg"),
                "diameter_min_mm": diam.get("min"),
                "diameter_max_mm": diam.get("max"),
            }
        )
    elif analysis_app == "conveyor_object":
        obj = m.get("object") or {}
        bbox = obj.get("bbox_mm") or {}
        out.update(
            {
                "object_points_count": obj.get("points_count"),
                "object_bbox_length_mm": bbox.get("length"),
                "object_bbox_width_mm": bbox.get("width"),
                "object_bbox_height_mm": bbox.get("height"),
                "object_bbox_volume_m3": obj.get("bbox_volume_m3"),
            }
        )
    return out


def _apply_output_units(values: dict, cfg: dict) -> dict:
    out = dict(values or {})
    length_unit = str(cfg.get("length_unit", "mm") or "mm").lower()
    volume_unit = str(cfg.get("volume_unit", "m3") or "m3").lower()
    if length_unit not in {"mm", "m"}:
        length_unit = "mm"
    if volume_unit not in {"m3", "l", "mm3"}:
        volume_unit = "m3"

    def _conv_len(mm_val):
        if mm_val is None:
            return None
        try:
            mm = float(mm_val)
        except Exception:
            return None
        return mm / 1000.0 if length_unit == "m" else mm

    def _conv_vol(m3_val):
        if m3_val is None:
            return None
        try:
            m3 = float(m3_val)
        except Exception:
            return None
        if volume_unit == "l":
            return m3 * 1000.0
        if volume_unit == "mm3":
            return m3 * 1_000_000_000.0
        return m3

    out["unit_length"] = length_unit
    out["unit_volume"] = volume_unit
    out["length"] = _conv_len(out.get("length_mm"))
    out["diameter_start"] = _conv_len(out.get("diameter_start_mm"))
    out["diameter_end"] = _conv_len(out.get("diameter_end_mm"))
    out["diameter_avg"] = _conv_len(out.get("diameter_avg_mm"))
    out["diameter_min"] = _conv_len(out.get("diameter_min_mm"))
    out["diameter_max"] = _conv_len(out.get("diameter_max_mm"))
    out["object_bbox_length"] = _conv_len(out.get("object_bbox_length_mm"))
    out["object_bbox_width"] = _conv_len(out.get("object_bbox_width_mm"))
    out["object_bbox_height"] = _conv_len(out.get("object_bbox_height_mm"))
    out["volume"] = _conv_vol(out.get("volume_m3"))
    out["object_bbox_volume"] = _conv_vol(out.get("object_bbox_volume_m3"))
    return out


def _format_output_payload(values: dict, cfg: dict) -> str | dict:
    payload_mode = str(cfg.get("payload_mode", "ascii") or "ascii").lower()
    selected = [str(v) for v in (cfg.get("selected_fields") or []) if str(v).strip()]
    include_labels = bool(cfg.get("include_labels", False))
    sep = str(cfg.get("separator", ";") or ";")
    prefix = str(cfg.get("prefix", "") or "")
    suffix = str(cfg.get("suffix", "") or "")
    precision = int(max(0, min(8, int(cfg.get("float_precision", 2) or 2))))

    if payload_mode == "json":
        if selected:
            return {k: values.get(k) for k in selected}
        return dict(values)

    keys = selected if selected else list(values.keys())
    parts: list[str] = []
    for key in keys:
        val = values.get(key)
        if isinstance(val, float):
            txt = f"{val:.{precision}f}"
        elif val is None:
            txt = ""
        else:
            txt = str(val)
        parts.append(f"{key}={txt}" if include_labels else txt)
    return f"{prefix}{sep.join(parts)}{suffix}"


@router.post("/start-listening")
async def start_listening(request: Request):
    """Start listening for Picoscan UDP data"""
    try:
        # Use app-level receiver manager
        receiver_manager = request.app.state.receiver_manager
        if receiver_manager is None:
            raise Exception('Receiver manager not initialized')

        # Start listeners for devices that are not already present. Do not
        # blindly clear receivers to avoid race conditions and 'address in use'
        # errors when a listener is already bound.
        results = {}
        for device in device_manager.get_all_devices():
            listen_ip = "0.0.0.0"
            # Only start if not already present/listening
            if device.device_id in receiver_manager.receivers:
                results[device.device_id] = True
                continue

            ok = receiver_manager.start_listening(
                device.device_id,
                listen_ip,
                device.port,
                segments_per_scan=getattr(device, 'segments_per_scan', None),
                format_type=getattr(device, 'format_type', 'compact'),
                device_type=getattr(device, 'device_type', 'picoscan'),
                sensor_ip=getattr(device, 'ip_address', None),
            )
            results[device.device_id] = ok
        
        return {
            "message": "Started listening for Picoscan UDP data",
            "results": results,
            "info": "Picoscan will send data to port 2115"
        }
    except Exception as e:
        logger.error(f"Error starting: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/trigger/start")
async def start_trigger(request: Request):
    """Start acquisition session (profile recording)."""
    session = start_trigger_session(request.app)
    session["trigger_source"] = "manual"
    return {
        "recording": session.get("recording", False),
        "devices": session["devices"],
        "distance_mm": session["distance_mm"],
    }


@router.post("/trigger/stop")
async def stop_trigger(request: Request):
    """Stop acquisition session."""
    session = stop_trigger_session(request.app)
    session["trigger_source"] = "manual"
    return {"recording": False, "distance_mm": session.get("distance_mm", 0.0)}


@router.get("/analytics/results")
async def analytics_results(request: Request):
    session = _get_session(request)
    return {
        "analysis_app": session.get("analysis_app", (device_manager.analysis_settings or {}).get("active_app", "log")),
        "metrics": session.get("analysis_metrics"),
        "has_points": bool(session.get("analysis_points")),
        "analysis_duration_ms": session.get("analysis_duration_ms"),
        "analysis_timestamp_ms": session.get("analysis_timestamp_ms"),
    }


@router.post("/analytics/recompute")
async def analytics_recompute(request: Request):
    session = _get_session(request)
    if bool(session.get("recording", False)):
        raise HTTPException(status_code=400, detail="Stop acquisition before recompute")
    _run_analysis_for_session(session, request.app)
    return {
        "analysis_app": session.get("analysis_app", (device_manager.analysis_settings or {}).get("active_app", "log")),
        "metrics": session.get("analysis_metrics"),
        "has_points": bool(session.get("analysis_points")),
        "analysis_duration_ms": session.get("analysis_duration_ms"),
        "analysis_timestamp_ms": session.get("analysis_timestamp_ms"),
    }


@router.get("/analytics/augmented-cloud")
async def analytics_augmented_cloud(request: Request, max_points: int = 60000):
    session = _get_session(request)
    points = session.get("analysis_points") or []
    total_points = len(points)
    if max_points is not None and max_points > 0 and total_points > max_points:
        step = max(1, total_points // max_points)
        points = points[::step]
        if len(points) > max_points:
            points = points[:max_points]
    return {
        "points": points,
        "points_count": len(points),
        "total_points": total_points,
    }


@router.get("/history")
async def history_list():
    return list_measurements()


@router.get("/history/{meas_id}")
async def history_item(meas_id: str):
    item = get_measurement(meas_id)
    if not item:
        raise HTTPException(status_code=404, detail="Measurement not found")
    return item


@router.get("/trigger/status")
async def trigger_status(request: Request):
    session = _get_session(request)
    _sync_encoder_worker(session)
    tdc_state = getattr(request.app.state, "tdc_input_state", None)
    if tdc_state == 2:
        tdc_label = "HIGH"
    elif tdc_state == 1:
        tdc_label = "LOW"
    else:
        tdc_label = "UNKNOWN"
    return {
        "recording": session.get("recording", False),
        "distance_mm": session.get("distance_mm", 0.0),
        "speed_mps": session.get("speed_mps"),
        "encoder_rpm": session.get("encoder_rpm"),
        "encoder_speed_mps": session.get("encoder_speed_mps"),
        "profiling_distance_mm": session.get("profiling_distance_mm"),
        "profiles_count": session.get("profiles_count", 0),
        "points_count": len(session.get("last_points") or []),
        "last_update_ts": session.get("last_update_ts"),
        "tdc_input_state": tdc_label,
        "trigger_source": session.get("trigger_source", "manual"),
    }


@router.get("/trigger/status/stream")
async def trigger_status_stream(request: Request):
    async def event_generator():
        while True:
            session = _get_session(request)
            _sync_encoder_worker(session)
            tdc_state = getattr(request.app.state, "tdc_input_state", None)
            if tdc_state == 2:
                tdc_label = "HIGH"
            elif tdc_state == 1:
                tdc_label = "LOW"
            else:
                tdc_label = "UNKNOWN"
            payload = {
                "recording": session.get("recording", False),
                "distance_mm": session.get("distance_mm", 0.0),
                "speed_mps": session.get("speed_mps"),
                "encoder_rpm": session.get("encoder_rpm"),
                "encoder_speed_mps": session.get("encoder_speed_mps"),
                "profiling_distance_mm": session.get("profiling_distance_mm"),
                "profiles_count": session.get("profiles_count", 0),
                "points_count": len(session.get("last_points") or []),
                "last_update_ts": session.get("last_update_ts"),
                "tdc_input_state": tdc_label,
                "trigger_source": session.get("trigger_source", "manual"),
            }
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/trigger/latest-cloud")
async def trigger_latest_cloud(request: Request, max_points: int = 40000):
    session = _get_session(request)
    if not session.get("recording") and session.get("accumulated_points"):
        points = session.get("accumulated_points") or []
    else:
        points = session.get("last_points") or []
    total_points = len(points)
    if max_points is not None and max_points > 0 and total_points > max_points:
        step = max(1, total_points // max_points)
        points = points[::step]
        if len(points) > max_points:
            points = points[:max_points]
    return {
        "points": points,
        "points_count": len(points),
        "total_points": total_points,
        "distance_mm": session.get("distance_mm", 0.0),
        "profiles_count": session.get("profiles_count", 0),
        "recording": session.get("recording", False),
    }


@router.get("/trigger/latest-cloud/stream")
async def trigger_latest_cloud_stream(request: Request, max_points: int = 30000):
    async def event_generator():
        while True:
            session = _get_session(request)
            if not session.get("recording") and session.get("accumulated_points"):
                points = session.get("accumulated_points") or []
            else:
                points = session.get("last_points") or []
            total_points = len(points)
            if max_points is not None and max_points > 0 and total_points > max_points:
                step = max(1, total_points // max_points)
                points = points[::step]
                if len(points) > max_points:
                    points = points[:max_points]
            payload = {
                "points": points,
                "points_count": len(points),
                "total_points": total_points,
                "recording": session.get("recording", False),
            }
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/stop-listening")
async def stop_listening(request: Request):
    """Stop listening"""
    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    results = receiver_manager.stop_all()
    return {
        "message": "Stopped listening",
        "results": results
    }


@router.post("/receive-data")
async def receive_point_cloud_data(req: AcquisitionRequest, request: Request):
    """
    Receive point cloud data from Picoscan (Compact format)
    
    Picoscan wysyła UDP dane do twojego PC na porcie 2115
    """
    try:
        receiver_manager = request.app.state.receiver_manager
        if receiver_manager is None:
            raise HTTPException(status_code=500, detail="Receiver manager not initialized")

        point_clouds_to_merge = []
        if hasattr(receiver_manager, "get_point_clouds_for_devices"):
            latest_clouds = receiver_manager.get_point_clouds_for_devices(req.device_ids, num_segments=req.num_segments or 1)
        else:
            latest_clouds = receiver_manager.get_point_clouds(num_segments=req.num_segments or 1)
        for device_id in req.device_ids:
            device = device_manager.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
            points = latest_clouds.get(device_id)
            if points is None or len(points) == 0:
                continue
            point_clouds_to_merge.append((points, device.calibration))
        
        if not point_clouds_to_merge:
            raise HTTPException(status_code=400, detail="No data received - ensure Picoscan is sending UDP")
        
        merged = PointCloudProcessor.merge_point_clouds(point_clouds_to_merge)
        merged_points = _apply_configured_preview_filters(merged.points)
        filtered_cloud = PointCloud(merged_points)
        stats = PointCloudProcessor.calculate_statistics(filtered_cloud)
        total_points = len(filtered_cloud)
        
        return {
            "message": f"Received data from {len(point_clouds_to_merge)} device(s)",
            "total_points": total_points,
            "devices": req.device_ids,
            "segments_per_device": req.num_segments,
            "statistics": stats
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/receiver-status/{device_id}")
async def get_receiver_status(device_id: str, request: Request):
    """Get status of receiver"""
    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    receiver_info = receiver_manager.receivers.get(device_id)
    if not receiver_info:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        return {
            "device_id": device_id,
            "listening": False,
            "info": "Receiver not initialized"
        }
    receiver = receiver_info.get("receiver") if isinstance(receiver_info, dict) else receiver_info
    
    return {
        "device_id": device_id,
        "listening": bool(getattr(receiver, "connected", False)),
        "info": receiver.get_info() if hasattr(receiver, "get_info") else {}
    }


@router.get("/receiver-metrics/{device_id}")
async def get_receiver_metrics(device_id: str, request: Request):
    """Get receiver metrics (received frames/segments)"""
    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    receiver_info = receiver_manager.receivers.get(device_id)
    if not receiver_info:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        return {
            "device_id": device_id,
            "listening": False,
            "metrics": None,
            "info": "Receiver not initialized"
        }

    receiver = receiver_info.get("receiver") if isinstance(receiver_info, dict) else receiver_info
    metrics = receiver.get_metrics() if hasattr(receiver, "get_metrics") else {}

    return {
        "device_id": device_id,
        "listening": receiver.connected,
        "metrics": metrics
    }


@router.post("/test-receive/{device_id}")
async def test_receive(device_id: str, num_segments: int = 1):
    """Test receiving data from device with point cloud preview"""
    try:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        
        device_type = str(getattr(device, "device_type", "picoscan") or "picoscan").lower()
        if device_type == "lms4000":
            receiver = Lms4000Receiver(sensor_ip=device.ip_address, sensor_port=device.port)
            if not receiver.start_listening():
                raise HTTPException(status_code=400, detail=f"Failed to connect to LMS4000 {device.ip_address}:{device.port}")
            points = receiver.receive_point_cloud(max(1, num_segments))
            receiver.stop_listening()
            if points is None or len(points) == 0:
                return {
                    "device_id": device_id,
                    "status": "connected_but_no_data",
                    "message": f"Connected to LMS4000 {device.ip_address}:{device.port}, but no LMDscandata received",
                    "points": [],
                }
        else:
            # Create temporary receiver
            receiver = PicoscanReceiver(
                listen_ip="0.0.0.0",
                listen_port=device.port,
                format_type=getattr(device, "format_type", "compact"),
            )
            # Start listening
            if not receiver.start_listening():
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to listen on port {device.port}"
                )
            # Try to receive data
            segments = receiver.receive_segments(num_segments)
            receiver.stop_listening()
            if not segments:
                return {
                    "device_id": device_id,
                    "status": "listening_but_no_data",
                    "message": f"Port {device.port} is open and listening, but no data from Picoscan",
                    "info": "Check Picoscan settings - ensure it's configured to send UDP to this PC",
                    "points": []
                }
            # Convert to point cloud
            segment_list = _extract_segments(segments)
            points = receiver.segments_to_point_cloud(segment_list)
        
        # Convert numpy array to list for JSON serialization
        if hasattr(points, 'tolist'):
            points_list = points.tolist()
        else:
            points_list = list(points)
        
        return {
            "device_id": device_id,
            "status": "success",
            "points_received": len(points_list),
            "port": device.port,
            "points": points_list
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Test failed: {str(e)}")


@router.get("/live-preview/{device_id}")
async def live_preview(device_id: str, request: Request):
    """Stream live point cloud data from device using Server-Sent Events"""
    try:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        
        # Get receiver manager from app state
        receiver_manager = request.app.state.receiver_manager
        if not receiver_manager:
            raise HTTPException(status_code=500, detail="Receiver manager not initialized")
        
        async def event_generator():
            """Generate SSE events with point cloud data"""
            # Check if receiver already exists (from auto-start)
            receiver_info = receiver_manager.receivers.get(device_id)
            
            device_type = str(getattr(device, "device_type", "picoscan") or "picoscan").lower()
            if not receiver_info:
                # No active receiver - create temporary one
                if device_type == "lms4000":
                    receiver = Lms4000Receiver(sensor_ip=device.ip_address, sensor_port=device.port)
                    if not receiver.start_listening():
                        yield f"data: {json.dumps({'error': 'Failed to connect LMS4000 ' + str(device.ip_address) + ':' + str(device.port)})}\n\n"
                        return
                else:
                    receiver = PicoscanReceiver(
                        listen_ip="0.0.0.0",
                        listen_port=device.port,
                        format_type=getattr(device, "format_type", "compact"),
                    )
                    # Try to start listening
                    if not receiver.start_listening():
                        yield f"data: {json.dumps({'error': 'Failed to start listening on port ' + str(device.port)})}\n\n"
                        return
                
                own_receiver = True
            else:
                # Use existing receiver from auto-start
                if isinstance(receiver_info, dict) and "receiver" in receiver_info:
                    receiver = receiver_info["receiver"]
                else:
                    receiver = receiver_info
                own_receiver = False
            
            try:
                logger.info(f"Live preview started for {device_id}")
                
                # Stream data continuously
                frame_count = 0
                while True:
                    try:
                        if not own_receiver:
                            latest = receiver_manager.get_latest_point_cloud(device_id)
                            if not latest or latest.get("points") is None or len(latest.get("points")) == 0:
                                yield f"data: {json.dumps({'status': 'waiting', 'device_id': device_id})}\n\n"
                                await asyncio.sleep(0.05)
                                continue
                            points = latest["points"]
                            frame_number = latest.get("frame")
                        elif device_type == "lms4000":
                            points = receiver.receive_point_cloud(2)
                            if points is None or len(points) == 0:
                                yield f"data: {json.dumps({'status': 'waiting', 'device_id': device_id})}\n\n"
                                await asyncio.sleep(0.05)
                                continue
                            frame_number = None
                        else:
                            segments_per_scan = None
                            try:
                                device = device_manager.get_device(device_id)
                                segments_per_scan = (
                                    getattr(device, 'segments_per_scan', None)
                                    or device_manager.point_cloud_settings.get("segments_per_scan")
                                )
                            except Exception:
                                segments_per_scan = None
                            segments_to_get = segments_per_scan or 1
                            segments = receiver.receive_segments(num_segments=segments_to_get)
                            if not segments:
                                yield f"data: {json.dumps({'status': 'waiting', 'device_id': device_id})}\n\n"
                                await asyncio.sleep(0.05)
                                continue
                            segment_list = _extract_segments(segments)
                            points = receiver.segments_to_point_cloud(segment_list)
                            frame_number = None
                            try:
                                if segment_list and segment_list[0].get("Modules"):
                                    frame_number = segment_list[0]["Modules"][0].get("FrameNumber")
                            except Exception:
                                frame_number = None
                        
                        if hasattr(points, 'tolist'):
                            points_list = points.tolist()
                        else:
                            points_list = list(points)

                        frame_count += 1
                        data = {
                            "frame": frame_number if frame_number is not None else frame_count,
                            "points": points_list,
                            "count": len(points_list),
                            "device_id": device_id,
                            "status": "streaming"
                        }
                        
                        yield f"data: {json.dumps(data)}\n\n"

                        # Small non-blocking delay to prevent CPU spinning
                        await asyncio.sleep(0.01)
                        
                    except Exception as e:
                        logger.debug(f"Error in streaming loop: {e}")
                        yield f"data: {json.dumps({'error': str(e)})}\n\n"
                        await asyncio.sleep(0.5)
                    except asyncio.CancelledError:
                        # Generator was cancelled (server shutdown / client disconnect)
                        break

                
            
            except Exception as e:
                logger.error(f"Live preview error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            
            finally:
                # Only close if this endpoint created the receiver
                if own_receiver:
                    try:
                        receiver.stop_listening()
                    except:
                        pass
        
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
