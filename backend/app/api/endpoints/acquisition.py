from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import List, Generator
from pydantic import BaseModel
from app.core.device_manager import device_manager
from app.analysis.log_measurement import compute_log_metrics, build_augmented_cloud
from app.analysis.conveyor_measurement import compute_conveyor_object_metrics, build_conveyor_augmented_cloud
from app.analysis.history_store import save_measurement, list_measurements, get_measurement
from app.analysis.measurement_quality import measurement_quality
from app.services.picoscan_receiver import PicoscanReceiver, PicoscanReceiverManager
from app.services.lms4000_receiver import Lms4000Receiver
from app.services.point_cloud_processor import PointCloudProcessor, PointCloud
from app.services import tdc_rest
from app.services.capture_pipeline import MotionHistory, FrameSynchronizer
from collections import deque
import logging
import json
import time
import asyncio
import numpy as np
import threading
import base64
import copy
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
router = APIRouter()
SESSION_TIMING_WINDOW = 240
PREVIEW_CACHE_MAX_POINTS = 40000
PREVIEW_SERIALIZE_INTERVAL_S = 0.2


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
            "captured_profiles": [],
            "captured_points_count": 0,
            "pending_recording_frames": [],
            "captured_full_points_np": None,
            "captured_full_cache_dirty": False,
            "captured_preview_points_np": None,
            "captured_preview_blob_b64": "",
            "captured_preview_blob_stride": 4,
            "captured_preview_cache_dirty": False,
            "captured_preview_last_encode_ts": 0.0,
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
            "perf_loop_ms": [],
            "perf_max_profiles_per_cycle": 0,
            "perf_last_profiles_added": 0,
            "perf_total_updates": 0,
            "archive_last_duration_ms": None,
            "archive_last_points_count": 0,
            "archive_last_ts": None,
            "archive_pending": False,
            "archive_last_error": None,
            "last_points_np": None,
            "last_points_emit_ts": 0.0,
            "perf_stage_samples": {},
            "perf_latest_cloud_request_ms": [],
            "perf_latest_cloud_stream_request_ms": [],
        }
        app.state.acquisition_session = session
    # Ensure keys exist also for sessions initialized in app startup
    session.setdefault("encoder_thread", None)
    session.setdefault("encoder_stop_event", None)
    session.setdefault("encoder_rpm", None)
    session.setdefault("encoder_speed_mps", None)
    session.setdefault("encoder_last_data", None)
    session.setdefault("analysis_timestamp_ms", None)
    session.setdefault("perf_loop_ms", [])
    session.setdefault("perf_max_profiles_per_cycle", 0)
    session.setdefault("perf_last_profiles_added", 0)
    session.setdefault("perf_total_updates", 0)
    session.setdefault("archive_last_duration_ms", None)
    session.setdefault("archive_last_points_count", 0)
    session.setdefault("archive_last_ts", None)
    session.setdefault("last_points_np", None)
    session.setdefault("last_points_emit_ts", 0.0)
    session.setdefault("captured_profiles", [])
    session.setdefault("captured_points_count", 0)
    session.setdefault("pending_recording_frames", [])
    session.setdefault("captured_full_points_np", None)
    session.setdefault("captured_full_cache_dirty", False)
    session.setdefault("captured_preview_points_np", None)
    session.setdefault("captured_preview_blob_b64", "")
    session.setdefault("captured_preview_blob_stride", 4)
    session.setdefault("captured_preview_cache_dirty", False)
    session.setdefault("captured_preview_last_encode_ts", 0.0)
    session.setdefault("archive_pending", False)
    session.setdefault("archive_last_error", None)
    session.setdefault("perf_stage_samples", {})
    session.setdefault("perf_latest_cloud_request_ms", [])
    session.setdefault("perf_latest_cloud_stream_request_ms", [])
    return session


def _get_session(request: Request) -> dict:
    return _get_session_from_app(request.app)


def _resolve_tdc_label(app) -> str:
    tdc_enabled = bool((device_manager.tdc_settings or {}).get("enabled", False))
    if not tdc_enabled:
        return "DISABLED"
    tdc_state = getattr(app.state, "tdc_input_state", None)
    stamp = getattr(app.state, 'tdc_input_monotonic', None)
    if stamp is None or time.monotonic() - stamp > 0.5:
        return 'UNKNOWN'
    if tdc_state == 2:
        return "HIGH"
    if tdc_state == 1:
        return "LOW"
    return "UNKNOWN"


def _record_session_stage(session: dict, stage: str, duration_ms: float):
    samples_map = session.get("perf_stage_samples")
    if not isinstance(samples_map, dict):
        samples_map = {}
        session["perf_stage_samples"] = samples_map
    samples = samples_map.get(stage)
    if not isinstance(samples, list):
        samples = []
        samples_map[stage] = samples
    samples.append(float(duration_ms))
    if len(samples) > SESSION_TIMING_WINDOW:
        del samples[:-SESSION_TIMING_WINDOW]


def _summarize_session_stage_timings(session: dict) -> dict:
    samples_map = session.get("perf_stage_samples")
    if not isinstance(samples_map, dict):
        return {}
    summary: dict = {}
    for stage, samples in samples_map.items():
        if not isinstance(samples, list) or len(samples) == 0:
            continue
        arr = np.asarray(samples, dtype=np.float64)
        if arr.size == 0:
            continue
        summary[stage] = {
            "samples": int(arr.size),
            "last_ms": float(arr[-1]),
            "mean_ms": float(np.mean(arr)),
            "p95_ms": float(np.percentile(arr, 95)),
        }
    return summary


def _downsample_points_np(points: np.ndarray | None, max_points: int) -> np.ndarray:
    if points is None:
        return np.empty((0, 4), dtype=np.float32)
    arr = np.asarray(points, dtype=np.float32)
    if arr.ndim != 2:
        return np.empty((0, 4), dtype=np.float32)
    if max_points <= 0 or len(arr) <= max_points:
        return np.array(arr, copy=True)
    step = max(1, len(arr) // max_points)
    sampled = arr[::step]
    if len(sampled) > max_points:
        sampled = sampled[:max_points]
    return np.array(sampled, copy=True)


def _append_profile_to_session_cache(session: dict, profile_points: np.ndarray):
    profile_np = np.asarray(profile_points, dtype=np.float32)
    if profile_np.ndim != 2 or profile_np.shape[0] == 0:
        return
    captured_profiles = session.get("captured_profiles")
    if not isinstance(captured_profiles, list):
        captured_profiles = []
        session["captured_profiles"] = captured_profiles
    captured_profiles.append(profile_np)
    session["captured_points_count"] = int(session.get("captured_points_count", 0) + int(profile_np.shape[0]))
    session["captured_full_cache_dirty"] = True

    existing_preview = session.get("captured_preview_points_np")
    if isinstance(existing_preview, np.ndarray) and existing_preview.ndim == 2 and existing_preview.shape[0] > 0:
        combined = np.vstack([existing_preview, profile_np])
    else:
        combined = profile_np
    session["captured_preview_points_np"] = _downsample_points_np(combined, PREVIEW_CACHE_MAX_POINTS)
    session["captured_preview_cache_dirty"] = True
    _maybe_refresh_captured_preview_blob(session, force=False)


def _get_captured_preview_points(session: dict) -> np.ndarray:
    preview_np = session.get("captured_preview_points_np")
    if isinstance(preview_np, np.ndarray) and preview_np.ndim == 2:
        return preview_np
    return np.empty((0, 4), dtype=np.float32)


def _empty_preview_blob_payload() -> dict:
    return {
        "points_blob_b64": "",
        "points_encoding": "float32_base64",
        "point_stride": 4,
        "points_count": 0,
    }


def _maybe_refresh_captured_preview_blob(session: dict, force: bool) -> dict:
    preview_np = _get_captured_preview_points(session)
    if preview_np.shape[0] == 0:
        session["captured_preview_blob_b64"] = ""
        session["captured_preview_blob_stride"] = 4
        session["captured_preview_cache_dirty"] = False
        return _empty_preview_blob_payload()
    dirty = bool(session.get("captured_preview_cache_dirty", True))
    cached_blob = session.get("captured_preview_blob_b64")
    cached_stride = int(session.get("captured_preview_blob_stride", 4) or 4)
    if not dirty:
        if isinstance(cached_blob, str):
            return {
                "points_blob_b64": cached_blob,
                "points_encoding": "float32_base64",
                "point_stride": cached_stride,
                "points_count": int(preview_np.shape[0]),
            }
    now = time.time()
    last_ts = float(session.get("captured_preview_last_encode_ts", 0.0) or 0.0)
    if (not force) and isinstance(cached_blob, str) and cached_blob and (now - last_ts) < PREVIEW_SERIALIZE_INTERVAL_S:
        return {
            "points_blob_b64": cached_blob,
            "points_encoding": "float32_base64",
            "point_stride": cached_stride,
            "points_count": int(preview_np.shape[0]),
        }
    t0 = time.perf_counter()
    packed = np.ascontiguousarray(preview_np, dtype=np.float32)
    blob_b64 = base64.b64encode(packed.tobytes()).decode("ascii")
    _record_session_stage(session, "serialize_captured_preview", (time.perf_counter() - t0) * 1000.0)
    session["captured_preview_blob_b64"] = blob_b64
    session["captured_preview_blob_stride"] = int(packed.shape[1]) if packed.ndim == 2 and packed.shape[1] > 0 else 4
    session["captured_preview_cache_dirty"] = False
    session["captured_preview_last_encode_ts"] = now
    return {
        "points_blob_b64": blob_b64,
        "points_encoding": "float32_base64",
        "point_stride": int(session["captured_preview_blob_stride"]),
        "points_count": int(packed.shape[0]),
    }


def _get_captured_preview_blob(session: dict) -> dict:
    if not bool(session.get("captured_preview_cache_dirty", True)):
        preview_np = _get_captured_preview_points(session)
        cached_blob = session.get("captured_preview_blob_b64")
        cached_stride = int(session.get("captured_preview_blob_stride", 4) or 4)
        if isinstance(cached_blob, str):
            return {
                "points_blob_b64": cached_blob,
                "points_encoding": "float32_base64",
                "point_stride": cached_stride,
                "points_count": int(preview_np.shape[0]),
            }
    return _maybe_refresh_captured_preview_blob(session, force=True)


def _get_captured_full_points_np(session: dict) -> np.ndarray:
    cached = session.get("captured_full_points_np")
    dirty = bool(session.get("captured_full_cache_dirty", False))
    if isinstance(cached, np.ndarray) and cached.ndim == 2 and not dirty:
        return cached
    chunks = session.get("captured_profiles")
    if not isinstance(chunks, list) or len(chunks) == 0:
        empty = np.empty((0, 4), dtype=np.float32)
        session["captured_full_points_np"] = empty
        session["captured_full_cache_dirty"] = False
        return empty
    arrays = [c for c in chunks if isinstance(c, np.ndarray) and c.ndim == 2 and c.shape[0] > 0]
    if not arrays:
        empty = np.empty((0, 4), dtype=np.float32)
        session["captured_full_points_np"] = empty
        session["captured_full_cache_dirty"] = False
        return empty
    t0 = time.perf_counter()
    full = np.vstack(arrays).astype(np.float32, copy=False)
    _record_session_stage(session, "materialize_captured_full_points", (time.perf_counter() - t0) * 1000.0)
    session["captured_full_points_np"] = full
    session["captured_full_cache_dirty"] = False
    return full


def _calc_speed_from_encoder_rpm(rpm: float, motion: dict) -> float | None:
    encoder_mode = motion.get("encoder_wheel_mode", "diameter") or "diameter"
    encoder_value_mm = float(motion.get("encoder_wheel_value_mm", 0.0) or 0.0)
    if not np.isfinite(encoder_value_mm) or encoder_value_mm <= 0:
        return None
    if encoder_mode == "circumference":
        circ_m = encoder_value_mm / 1000.0
    else:
        circ_m = (encoder_value_mm * np.pi) / 1000.0
    # TDC velocity is RPM (rotations per minute)
    rps = float(rpm) / 60.0
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
        value = float(raw)
        return value if np.isfinite(value) else None
    except Exception:
        return None


def _set_encoder_error(session, code, message):
    if session.get("encoder_error") != code:
        logger.warning("Encoder status: %s: %s", code, message)
    session["encoder_error"] = code
    session["encoder_error_message"] = message


def _run_encoder_loop(session: dict):
    stop_event = session.get("encoder_stop_event")
    history = session.setdefault("motion_history", MotionHistory())
    previous_config = None
    while stop_event and not stop_event.is_set():
        motion = device_manager.motion_settings or {}
        if (motion.get("mode", "fixed") or "fixed") != "encoder":
            session["encoder_rpm"] = None
            session["encoder_speed_mps"] = None
            time.sleep(0.2)
            continue

        tdc_cfg = device_manager.tdc_settings or {}
        config = (motion.get("encoder_wheel_mode"), motion.get("encoder_wheel_value_mm"),
                  tdc_cfg.get("ip_address"), tdc_cfg.get("encoder_port"))
        if previous_config is not None and config != previous_config:
            history.add(time.monotonic(), None)
        previous_config = config
        poll_interval_ms = max(50, int(tdc_cfg.get("poll_interval_ms", 200) or 200))
        if not bool(tdc_cfg.get("enabled", False)):
            session["encoder_rpm"] = None
            session["encoder_speed_mps"] = None
            history.add(time.monotonic(), None)
            _set_encoder_error(session, "encoder_disabled", "TDC encoder polling is disabled.")
            time.sleep(poll_interval_ms / 1000.0)
            continue

        try:
            request_started = time.monotonic()
            payload = tdc_rest.fetch_encoder_process_data(data_format="iodd")
            received = time.monotonic()
            rpm = _extract_encoder_rpm(payload)
            speed_mps = _calc_speed_from_encoder_rpm(rpm, motion) if rpm is not None else None
            if rpm is None:
                _set_encoder_error(session, "encoder_velocity_missing",
                    "Missing decoded Velocity. Check encoder IODD on TDC and IO-Link port; byteArray is not a speed reading.")
            elif speed_mps is None or not np.isfinite(speed_mps):
                speed_mps = None
                _set_encoder_error(session, "encoder_wheel_invalid", "Encoder wheel circumference/diameter must be finite and positive.")
            elif speed_mps < 0:
                _set_encoder_error(session, "reverse_motion", "Reverse encoder motion is not supported for length measurement.")
            elif received - request_started > 0.5:
                speed_mps = None
                _set_encoder_error(session, "encoder_latency", "Encoder response exceeded 500 ms; sample timing is unreliable.")
            else:
                session["encoder_error"] = None
                session["encoder_error_message"] = None
            # Response receipt is a host-time estimate, not a hardware encoder timestamp.
            history.add(received, speed_mps)
            session["encoder_last_data"] = payload
            session["encoder_rpm"] = rpm
            session["encoder_speed_mps"] = speed_mps
            session["encoder_sample_monotonic"] = received
            # Keep live speed visible even when trigger recording is stopped.
            session["speed_mps"] = speed_mps
        except Exception as exc:
            history.add(time.monotonic(), None)
            _set_encoder_error(session, "encoder_read_failed", "TDC encoder read failed (" + type(exc).__name__ + "). Check connection, token and IO-Link port.")
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


_session_lock = threading.RLock()


def _update_session_once(session: dict, receiver_manager):
    with _session_lock:
        if session.get("recording"):
            _update_session_locked(session, receiver_manager)


def _capture_quality(session):
    return measurement_quality(session)


def _position_for_frame(session, stamp):
    motion = session["capture_motion"]
    start = session["capture_started_monotonic"]
    if motion.get("mode", "fixed") == "fixed":
        speed = motion.get("fixed_speed_mps")
        if speed is None or not np.isfinite(speed) or speed < 0:
            return None, "motion_configuration_invalid"
        return (stamp - start) * float(speed) * 1000, None
    history = session["motion_history"]
    origin = session.get("capture_motion_origin")
    if origin is None:
        origin, error = history.position(start)
        if error:
            return None, error
        session["capture_motion_origin"] = origin
    target, error = history.position(stamp)
    if error:
        return None, error
    if origin[1] != target[1]:
        return None, "encoder_history_gap"
    return target[0] - origin[0], None


def _consume_positioned_groups(session, final=False):
    issues = session.setdefault("quality_issues", set())
    pending = session["pending_profile_groups"]
    added = 0
    while pending:
        group = pending[0]
        positions = {}
        errors = []
        for device_id, frame in group.items():
            position, error = _position_for_frame(session, frame["received_monotonic"])
            positions[device_id] = position
            if error:
                errors.append(error)
        if "pending" in errors and not final:
            break  # Wait for the next encoder sample instead of extrapolating speed.
        pending.popleft()
        if errors:
            issues.update("encoder_unbracketed_frame" if e == "pending" else e for e in errors)
            continue
        distance = sum(positions.values()) / len(positions)
        session["distance_mm"] = max(session.get("distance_mm", 0.0), distance)
        spacing = session["profiling_distance_mm"]
        if distance < session.get("start_delay_mm_remaining", 0.0):
            continue
        if distance - session.get("last_profile_distance_mm", 0.0) < spacing:
            continue
        clouds = []
        for device_id, frame in group.items():
            calibration = session["capture_calibrations"].get(device_id)
            if calibration is None:
                issues.add("device_configuration_missing")
                break
            merged = PointCloudProcessor.merge_point_clouds([(frame["points"], calibration)])
            points = np.asarray(merged.points, dtype=np.float32).copy()
            if not len(points):
                issues.add("empty_scanner_frame")
                break
            if getattr(merged, "intensities", None) is not None and len(merged.intensities) == len(points):
                points = np.column_stack([points, merged.intensities])
            elif points.shape[1] == 3:
                points = np.column_stack([points, np.zeros(len(points), dtype=np.float32)])
            # Each scanner retains its own interpolated position, including pairing skew.
            points[:, 1] = positions[device_id]
            clouds.append(points)
        else:
            profile = np.vstack(clouds).astype(np.float32)
            _append_profile_to_session_cache(session, profile)
            session.setdefault("captured_profile_positions", []).append(distance)
            session["profiles_count"] = session.get("profiles_count", 0) + 1
            session["last_profile_distance_mm"] = distance
            session["last_points_np"] = profile
            added += 1
    return added


def _update_session_locked(session: dict, receiver_manager):
    if receiver_manager is None:
        return
    t_loop_start = time.perf_counter()
    issues = session.setdefault("quality_issues", set())
    try:
        _sync_encoder_worker(session)
        now = time.monotonic()
        motion = session["capture_motion"]
        if dict(device_manager.motion_settings or {}) != motion:
            issues.add("motion_configuration_changed")
        session["last_update_ts"] = time.time()
        if motion.get("mode", "fixed") == "encoder":
            age = now - session.get("encoder_sample_monotonic", float("-inf"))
            speed = session.get("encoder_speed_mps")
            if age > 0.5 or speed is None:
                issues.add("encoder_stale")
                if session.get("encoder_error"):
                    issues.add(session["encoder_error"])
                speed = None
            session["speed_mps"] = speed
        else:
            session["speed_mps"] = motion.get("fixed_speed_mps")
        sync = session["frame_synchronizer"]
        health = receiver_manager.get_health_snapshot()
        for device_id in session["devices"]:
            current = health.get(device_id, {})
            baseline = session["receiver_health_baseline"].get(device_id, {})
            if (baseline.get('stream_epoch') is not None
                    and current.get('stream_epoch') != baseline['stream_epoch']):
                issues.add('scanner_restarted')
            for counter, issue in (("incomplete_frames_dropped", "incomplete_frame"),
                                   ("ingress_queue_dropped", "udp_ingress_overflow"),
                                   ("processing_queue_dropped", "scanner_processing_overflow"),
                                   ("queue_overflows", "frame_queue_overflow"),
                                   ("late_complete_frames_dropped", "late_frame"),
                                   ("device_frame_gaps", "frames_skipped"),
                                   ("device_frame_regressions", "scanner_restarted"),
                                   ("pattern_learning_frames_dropped", "segment_pattern_unverified")):
                if current.get(counter, 0) > baseline.get(counter, 0):
                    issues.add(issue)
            frames = receiver_manager.drain_point_clouds(device_id)
            stop_stamp = session.get("capture_stopped_monotonic")
            if stop_stamp is not None:
                frames = [f for f in frames if f.get("received_monotonic", float("inf")) <= stop_stamp]
            if frames:
                session["device_last_frame_monotonic"][device_id] = max(
                    frame.get("received_monotonic", 0.0) for frame in frames)
            if now - session["device_last_frame_monotonic"].get(device_id, session["capture_started_monotonic"]) > 0.5:
                issues.add("scanner_missing")
            sync.add(device_id, frames, session["capture_started_monotonic"], issues)
        groups = sync.ready(now, issues)
        pending = session["pending_profile_groups"]
        for group in groups:
            if len(pending) >= 256:
                pending.popleft()
                issues.add("position_queue_overflow")
            pending.append(group)
        added = _consume_positioned_groups(session)
        stamp = now if motion.get("mode", "fixed") == "fixed" else session.get("encoder_sample_monotonic", now)
        if session.get("capture_stopped_monotonic") is not None:
            stamp = min(stamp, session["capture_stopped_monotonic"])
        if stamp >= session["capture_started_monotonic"]:
            distance, error = _position_for_frame(session, stamp)
            if error and error != "pending":
                issues.add(error)
            elif distance is not None:
                session["distance_mm"] = max(session.get("distance_mm", 0.0), distance)
        session["perf_last_profiles_added"] = added
        session["perf_max_profiles_per_cycle"] = max(session.get("perf_max_profiles_per_cycle", 0), added)
        # Filtering affects only a copy for display, never captured measurement arrays.
        if now - session.get("last_points_emit_monotonic", 0.0) >= 0.15:
            points = session.get("last_points_np")
            if points is not None:
                session["last_points"] = _apply_configured_preview_filters(points.copy()).tolist()
            session["last_points_emit_monotonic"] = now
    except Exception:
        issues.add("acquisition_error")
        logger.exception("Acquisition pipeline failed")
    finally:
        _record_session_stage(session, "cycle", (time.perf_counter() - t_loop_start) * 1000)
        session["perf_total_updates"] = session.get("perf_total_updates", 0) + 1


def _run_acquisition_loop(app):
    session = app.state.acquisition_session
    stop_event = session.get("worker_stop_event")
    receiver_manager = app.state.receiver_manager
    while stop_event and not stop_event.is_set():
        if session.get("recording"):
            _update_session_once(session, receiver_manager)
            time.sleep(0.01)
        else:
            time.sleep(0.05)

def start_trigger_session(app):
    with _session_lock:
        return _start_trigger_session_locked(app)


def _start_trigger_session_locked(app):
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
    session["capture_started_ts"] = time.time()
    session["capture_started_monotonic"] = time.monotonic()
    session["capture_stopped_monotonic"] = None
    session["capture_motion_origin"] = None
    session["captured_profile_positions"] = []
    session.pop("measurement_encoder_error", None)
    session.pop("measurement_encoder_error_message", None)
    session["capture_motion"] = dict(device_manager.motion_settings or {})
    if session["capture_motion"].get("mode", "fixed") == "fixed":
        session["encoder_error"] = None
        session["encoder_error_message"] = None
    session.setdefault("motion_history", MotionHistory())
    session["pending_profile_groups"] = deque()
    session["device_last_frame_monotonic"] = {}
    session["profiling_distance_mm"] = float(session["capture_motion"].get("profiling_distance_mm") or 10.0)
    session["last_motion_monotonic"] = time.monotonic()
    session["consumed_frames"] = {}
    session["quality_issues"] = set()
    session["measurement_valid"] = False
    session["analysis_pending"] = False
    session["captured_profiles"] = []
    session["captured_points_count"] = 0
    session["pending_recording_frames"] = []
    session["captured_full_points_np"] = None
    session["captured_full_cache_dirty"] = False
    session["captured_preview_points_np"] = None
    session["captured_preview_blob_b64"] = ""
    session["captured_preview_blob_stride"] = 4
    session["captured_preview_cache_dirty"] = False
    session["captured_preview_last_encode_ts"] = 0.0
    session["last_profile_distance_mm"] = 0.0
    session["profiles_count"] = 0
    # Clear previous run analysis state to avoid stale results being shown.
    session["analysis_metrics"] = None
    session["analysis_points"] = []
    session["analysis_duration_ms"] = None
    session["analysis_timestamp_ms"] = None
    session["archive_last_duration_ms"] = None
    session["archive_last_points_count"] = 0
    session["archive_last_ts"] = None
    session["archive_pending"] = False
    session["archive_last_error"] = None
    session["last_points_np"] = None
    session["last_points_emit_ts"] = 0.0
    tdc_cfg = device_manager.tdc_settings or {}
    session["start_delay_mm_remaining"] = (
        float(tdc_cfg.get("start_delay_mm", 0) or 0)
        if tdc_cfg.get("enabled") and tdc_cfg.get("start_delay_mode") == "distance" else 0.0
    )
    session["devices"] = [d.device_id for d in device_manager.get_all_devices() if d.enabled]
    session["capture_calibrations"] = {
        d.device_id: copy.deepcopy(d.calibration) for d in device_manager.get_all_devices() if d.enabled
    }
    session["frame_synchronizer"] = FrameSynchronizer(session["devices"])
    session["receiver_health_baseline"] = app.state.receiver_manager.get_health_snapshot()
    if not session["devices"]:
        session["quality_issues"].add("no_devices")
    # Discard idle backlog. Samples received after start remain timestamp-gated.
    for device_id in session["devices"]:
        frames = app.state.receiver_manager.drain_point_clouds(device_id)
        session["frame_synchronizer"].add(device_id, frames, session["capture_started_monotonic"], session["quality_issues"])
    _sync_encoder_worker(session)
    return session


def _save_measurement_async(session: dict, payload: dict):
    def publish_archive_state():
        target = session.get("archive_live_session")
        if target is None:
            return
        with _session_lock:
            if target.get("capture_started_ts") == session.get("capture_started_ts"):
                for key in ("archive_pending", "archive_last_duration_ms", "archive_last_points_count",
                            "archive_last_ts", "archive_last_error"):
                    target[key] = session.get(key)

    def worker():
        t_archive_start = time.perf_counter()
        try:
            save_measurement(payload)
            session["archive_last_duration_ms"] = int((time.perf_counter() - t_archive_start) * 1000)
            session["archive_last_points_count"] = int(len(payload.get("original_points") or []))
            session["archive_last_ts"] = int(time.time() * 1000)
            session["archive_last_error"] = None
        except Exception as exc:
            session["archive_last_error"] = str(exc)
        finally:
            session["archive_pending"] = False
            publish_archive_state()

    session["archive_pending"] = True
    publish_archive_state()
    threading.Thread(target=worker, daemon=True).start()


def _run_analysis_for_session(session: dict, app):
    try:
        t0 = time.time()
        points_np = _get_captured_full_points_np(session)
        points = points_np.tolist() if isinstance(points_np, np.ndarray) and points_np.shape[0] > 0 else []
        # A preview without measured motion is not a valid length measurement.
        profiling_distance_mm = session.get("profiling_distance_mm") or 10.0
        analysis_cfg = device_manager.analysis_settings or {}
        analysis_app = str(analysis_cfg.get("active_app", "log") or "log").strip().lower()
        if analysis_app not in {"log", "conveyor_object", "none"}:
            analysis_app = "log"
        session["analysis_timestamp_ms"] = int(time.time() * 1000)
        session["analysis_app"] = analysis_app
        if points:
            metrics_for_save = None
            if analysis_app == "log":
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
                    profile_positions=session.get("captured_profile_positions"),
                    profile_ids=(np.repeat(np.arange(len(session.get("captured_profiles", []))),
                        [len(p) for p in session.get("captured_profiles", [])])
                        if session.get("captured_profile_positions") else None),
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
            elif analysis_app == "conveyor_object":
                metrics = compute_conveyor_object_metrics(
                    points,
                    plane_quantile=float(analysis_cfg.get("conveyor_plane_quantile", 0.35) or 0.35),
                    plane_inlier_mm=float(analysis_cfg.get("conveyor_plane_inlier_mm", 8.0) or 8.0),
                    object_min_height_mm=float(analysis_cfg.get("conveyor_object_min_height_mm", 8.0) or 8.0),
                    localization_algorithm=str(
                        analysis_cfg.get("conveyor_localization_algorithm", "object_cloud_bbox") or "object_cloud_bbox"
                    ),
                    top_plane_quantile=float(analysis_cfg.get("conveyor_top_plane_quantile", 0.88) or 0.88),
                    top_plane_inlier_mm=float(analysis_cfg.get("conveyor_top_plane_inlier_mm", 4.0) or 4.0),
                    denoise_enabled=bool(analysis_cfg.get("conveyor_denoise_enabled", True)),
                    denoise_cell_mm=float(analysis_cfg.get("conveyor_denoise_cell_mm", 8.0) or 8.0),
                    denoise_min_points_per_cell=int(
                        analysis_cfg.get("conveyor_denoise_min_points_per_cell", 3) or 3
                    ),
                    keep_largest_component=bool(analysis_cfg.get("conveyor_keep_largest_component", True)),
                )
                session["analysis_metrics"] = metrics
                metrics_for_save = metrics
                session["analysis_points"] = build_conveyor_augmented_cloud(
                    points,
                    metrics,
                    max_points=int(analysis_cfg.get("conveyor_object_max_points", 60000) or 60000),
                )
            else:
                # "none" mode: keep only acquisition cloud, skip metric analysis.
                session["analysis_metrics"] = None
                session["analysis_points"] = []
                metrics_for_save = None
            session["analysis_duration_ms"] = int((time.time() - t0) * 1000)
            session["measurement_valid"] = not bool(session.get("quality_issues"))
            try:
                notifier = app.state.tcp_notifier
            except Exception:
                notifier = None
            output_cfg = dict(device_manager.output_settings or {})
            if notifier and bool(output_cfg.get("enabled", False)) and analysis_app != "none" and _capture_quality(session)["measurement_valid"]:
                values = _flatten_output_values(session, analysis_app, metrics_for_save or session.get("analysis_metrics"))
                values = _apply_output_units(values, output_cfg)
                payload = _format_output_payload(values, output_cfg)
                notifier.broadcast(payload)
            payload = {
                "analysis_app": analysis_app,
                "metrics": metrics_for_save or session.get("analysis_metrics"),
                "original_points": points,
                "augmented_points": session["analysis_points"],
                "analysis_timestamp_ms": session.get("analysis_timestamp_ms"),
                "profiling_distance_mm": profiling_distance_mm,
                "profiles_count": session.get("profiles_count", 0),
                "profile_positions_mm": session.get("captured_profile_positions", []),
                "profile_point_counts": [len(p) for p in session.get("captured_profiles", [])],
                "distance_mm": session.get("distance_mm", 0.0),
                "devices": session.get("devices", []),
                "analysis_duration_ms": session.get("analysis_duration_ms"),
                **_capture_quality(session),
            }
            _save_measurement_async(session, payload)
            logger.info(
                "Analysis queued for archive: app=%s points=%s profiles=%s duration_ms=%s",
                analysis_app,
                int(len(points)),
                int(session.get("profiles_count", 0) or 0),
                session.get("analysis_duration_ms"),
            )
        else:
            session.setdefault("quality_issues", set()).add("no_profiles")
            session["measurement_valid"] = False
            session["analysis_metrics"] = None
            session["analysis_points"] = []
            session["analysis_duration_ms"] = None
            session["analysis_timestamp_ms"] = None
            _save_measurement_async(session, {
                "analysis_app": analysis_app, "metrics": None, "original_points": [],
                "augmented_points": [], "profiles_count": 0,
                "distance_mm": session.get("distance_mm", 0.0),
                "devices": session.get("devices", []), **_capture_quality(session),
            })
            logger.info("Analysis skipped: no points available for current session")
    except Exception as exc:
        logger.exception("Session analysis failed")
        issue = ("analysis_insufficient_data" if isinstance(exc, ValueError)
                 and str(exc) == "Insufficient data for circle fitting." else "analysis_failed")
        session.setdefault("quality_issues", set()).add(issue)
        session["measurement_valid"] = False
        session["analysis_metrics"] = None
        session["analysis_points"] = []
        session["analysis_duration_ms"] = None
        session["analysis_timestamp_ms"] = None
        _save_measurement_async(session, {
            "analysis_app": session.get("analysis_app", "none"), "metrics": None,
            "original_points": _get_captured_full_points_np(session).tolist(), "augmented_points": [],
            "profiles_count": session.get("profiles_count", 0),
            "distance_mm": session.get("distance_mm", 0.0),
            "devices": session.get("devices", []), **_capture_quality(session),
        })


def stop_trigger_session(app):
    with _session_lock:
        return _stop_trigger_session_locked(app)


def _stop_trigger_session_locked(app):
    """Stop acquisition session for internal calls."""
    session = _get_session_from_app(app)
    if not session.get("recording"):
        return session
    session["capture_stopped_monotonic"] = time.monotonic()
    # Try one final data pull while still recording to reduce chance of missing
    # the last profile at stop edge.
    try:
        _update_session_once(session, app.state.receiver_manager)
    except Exception:
        pass
    session["recording"] = False
    session["measurement_encoder_error"] = session.get("encoder_error")
    session["measurement_encoder_error_message"] = session.get("encoder_error_message")
    _sync_encoder_worker(session)
    # Analyze an isolated snapshot so trigger polling can continue immediately.
    snapshot = dict(session)
    snapshot["archive_live_session"] = session
    snapshot["captured_profiles"] = list(session.get("captured_profiles", []))
    snapshot["captured_profile_positions"] = list(session.get("captured_profile_positions", []))
    snapshot["quality_issues"] = set(session.get("quality_issues", set()))
    snapshot["pending_profile_groups"] = deque(session.get("pending_profile_groups", []))
    sync = session["frame_synchronizer"]
    if any(q and q[0]["received_monotonic"] < session["capture_stopped_monotonic"] - sync.tolerance
           for q in sync.queues.values()):
        snapshot["quality_issues"].add("scanner_unmatched_at_stop")
    snapshot["analysis_pending"] = False
    capture_id = session.get("capture_started_ts")
    session["analysis_pending"] = True

    def analyze():
        try:
            # Wait only for encoder bracketing, never invent a final speed sample.
            deadline = time.monotonic() + 0.6
            while time.monotonic() < deadline:
                _consume_positioned_groups(snapshot)
                stop_distance, stop_error = _position_for_frame(snapshot, snapshot["capture_stopped_monotonic"])
                if not snapshot["pending_profile_groups"] and stop_error != "pending":
                    break
                time.sleep(0.02)
            _consume_positioned_groups(snapshot, final=True)
            stop_distance, stop_error = _position_for_frame(snapshot, snapshot["capture_stopped_monotonic"])
            if stop_error:
                snapshot["quality_issues"].add("encoder_unbracketed_stop" if stop_error == "pending" else stop_error)
            else:
                snapshot["distance_mm"] = stop_distance
        except Exception:
            logger.exception("Capture finalization failed")
            snapshot["quality_issues"].add("capture_finalization_failed")
            snapshot["measurement_valid"] = False
        try:
            if not snapshot.get("profiles_count"):
                snapshot["quality_issues"].add("no_profiles")
            _run_analysis_for_session(snapshot, app)
        finally:
            with _session_lock:
                if session.get("capture_started_ts") == capture_id:
                    session["analysis_pending"] = False
                    for key in ("analysis_metrics", "analysis_points", "analysis_duration_ms",
                                "analysis_timestamp_ms", "analysis_app", "quality_issues", "measurement_valid",
                                "captured_profiles", "captured_profile_positions", "captured_points_count",
                                "captured_full_points_np", "captured_full_cache_dirty",
                                "captured_preview_points_np", "profiles_count", "distance_mm"):
                        session[key] = snapshot.get(key)
                    # Snapshot may have encoded a different cloud: never reuse the old blob.
                    session["captured_preview_cache_dirty"] = True
                    session["captured_preview_blob_b64"] = ""


    threading.Thread(target=analyze, daemon=True, name="capture-analysis").start()
    session["pending_recording_frames"] = []
    return session


def _extract_segments(result):
    """Normalize scansegmentapi result to a list of segment dicts."""
    if not result:
        return []
    if isinstance(result, tuple):
        segments = result[0]
        return segments if isinstance(segments, list) else [segments]
    return result if isinstance(result, list) else [result]


def _uses_lmd_stream(device) -> bool:
    fmt = str(getattr(device, "format_type", "") or "").lower()
    dtype = str(getattr(device, "device_type", "picoscan") or "picoscan").lower()
    return fmt == "lmdscandata" or dtype == "lms4000"


def _try_scansegment_direct_udp(device, num_segments: int):
    """
    Direct fallback receive using ScanSegmentAPI over UDP.
    Tries compact/msgpack on likely ports and returns (points, meta) or (None, None).
    """
    primary_port = int(getattr(device, "port", 2115) or 2115)
    port_candidates = [primary_port]
    if primary_port != 2115:
        port_candidates.append(2115)
    for port in port_candidates:
        for fmt in ("compact", "msgpack"):
            receiver = PicoscanReceiver(listen_ip="0.0.0.0", listen_port=port, format_type=fmt)
            if not receiver.start_listening():
                continue
            try:
                segments = receiver.receive_segments(max(1, int(num_segments)))
                if not segments:
                    continue
                segment_list = _extract_segments(segments)
                points = receiver.segments_to_point_cloud(segment_list)
                if points is not None and len(points) > 0:
                    return points, {"transport": "udp", "format_type": fmt, "listen_port": port}
            finally:
                receiver.stop_listening()
    return None, None


def _device_availability_summary(receiver_manager) -> dict:
    devices = [d for d in device_manager.get_all_devices() if bool(getattr(d, "enabled", True))]
    health = receiver_manager.get_health_snapshot() if receiver_manager and hasattr(receiver_manager, "get_health_snapshot") else {}
    online = []
    offline = []
    unknown = []
    for d in devices:
        item = health.get(d.device_id, {})
        status = str(item.get("availability", "unknown") or "unknown")
        # If stream was recently updated, treat as online.
        age_s = item.get("latest_data_age_s")
        if isinstance(age_s, (int, float)) and age_s <= 2.5:
            status = "online"
        if status == "online":
            online.append(d.device_id)
        elif status == "offline":
            offline.append(d.device_id)
        else:
            unknown.append(d.device_id)
    return {
        "enabled_total": len(devices),
        "online_ids": online,
        "offline_ids": offline,
        "unknown_ids": unknown,
        "health": health,
    }


def _ensure_enabled_device_listeners(receiver_manager) -> None:
    if receiver_manager is None:
        return
    for device in device_manager.get_all_devices():
        if not bool(getattr(device, "enabled", True)):
            continue
        device_id = device.device_id
        info = receiver_manager.receivers.get(device_id)
        receiver_present = isinstance(info, dict) and bool(info.get("listening", False))
        if receiver_present:
            continue
        try:
            receiver_manager.start_listening(
                device.device_id,
                "0.0.0.0",
                device.port,
                segments_per_scan=getattr(device, "segments_per_scan", None),
                format_type=getattr(device, "format_type", "compact"),
                device_type=getattr(device, "device_type", "picoscan"),
                sensor_ip=getattr(device, "ip_address", None),
            )
        except Exception as exc:
            logger.warning("Listener ensure failed for %s: %s", device_id, exc)


def _estimate_speed_capability(session: dict, receiver_manager) -> dict:
    motion = device_manager.motion_settings or {}
    profiling_distance_mm = float(session.get("profiling_distance_mm") or motion.get("profiling_distance_mm") or 10.0)
    perf_loop = session.get("perf_loop_ms") or []
    if not perf_loop:
        return {
            "profiling_distance_mm": profiling_distance_mm,
            "error": "insufficient_runtime_data",
            "message": "Brak danych runtime. Uruchom akwizycję na kilka sekund, aby wyliczyć wiarygodny limit.",
        }

    loop_arr = np.asarray(perf_loop, dtype=np.float64)
    loop_mean_ms = float(np.mean(loop_arr))
    loop_p95_ms = float(np.percentile(loop_arr, 95))
    # Acquisition loop sleeps 50 ms after each update.
    loop_period_s = max(0.001, (loop_p95_ms / 1000.0) + 0.05)
    loop_rate_hz = 1.0 / loop_period_s
    # Quality-oriented limit: <= 1 profile per update cycle.
    max_profiles_quality = loop_rate_hz
    max_speed_quality_mps = (profiling_distance_mm / 1000.0) * max_profiles_quality
    recommended_speed_mps = max(0.0, max_speed_quality_mps * 0.8)

    max_profiles_per_cycle = int(session.get("perf_max_profiles_per_cycle", 0) or 0)
    if max_profiles_per_cycle > 1:
        overload_note = (
            "Wykryto wiele profili dopisywanych w jednej iteracji, co oznacza że układ okresowo nie nadążał."
        )
    else:
        overload_note = "Nie wykryto zaległości profilowania w pojedynczej iteracji."

    availability = _device_availability_summary(receiver_manager)
    online_count = len(availability["online_ids"])
    enabled_total = int(availability["enabled_total"])
    online_ratio = (online_count / enabled_total) if enabled_total > 0 else 0.0
    receiver_pipeline = {
        device_id: {
            "pipeline_timing_ms": (item or {}).get("pipeline_timing_ms") or {},
            "last_points_count": (item or {}).get("last_points_count"),
            "expected_segment_pattern": (item or {}).get("expected_segment_pattern") or [],
            "last_complete_segment_pattern": (item or {}).get("last_complete_segment_pattern") or [],
            "incomplete_frames_dropped": (item or {}).get("incomplete_frames_dropped", 0),
        }
        for device_id, item in (availability.get("health") or {}).items()
    }

    return {
        "profiling_distance_mm": profiling_distance_mm,
        "loop_stats": {
            "samples": int(len(loop_arr)),
            "mean_update_ms": loop_mean_ms,
            "p95_update_ms": loop_p95_ms,
            "effective_cycle_ms": loop_period_s * 1000.0,
            "effective_cycle_hz": loop_rate_hz,
            "max_profiles_per_cycle_seen": max_profiles_per_cycle,
        },
        "speed_limits": {
            "max_quality_mps": max_speed_quality_mps,
            "recommended_mps": recommended_speed_mps,
            "max_quality_mmps": max_speed_quality_mps * 1000.0,
            "recommended_mmps": recommended_speed_mps * 1000.0,
        },
        "archive_stats": {
            "last_archive_duration_ms": session.get("archive_last_duration_ms"),
            "last_archive_points_count": session.get("archive_last_points_count"),
            "last_archive_ts": session.get("archive_last_ts"),
            "archive_pending": bool(session.get("archive_pending", False)),
            "archive_last_error": session.get("archive_last_error"),
        },
        "pipeline_timing_ms": _summarize_session_stage_timings(session),
        "device_availability": {
            "enabled_total": enabled_total,
            "online": online_count,
            "online_ratio": online_ratio,
            "online_ids": availability["online_ids"],
            "offline_ids": availability["offline_ids"],
        },
        "receiver_pipeline": receiver_pipeline,
        "validation_notes": [
            overload_note,
            "Limit jakościowy liczony dla zasady 1 profil / iteracja pętli akwizycji.",
            "Dla bezpieczeństwa operacyjnego zalecane jest 80% limitu jakościowego.",
        ],
    }


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
                "diameter_middle_mm": diam.get("middle"),
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
    # Global unit-selected aliases.
    out["length"] = _conv_len(out.get("length_mm"))
    out["diameter_start"] = _conv_len(out.get("diameter_start_mm"))
    out["diameter_middle"] = _conv_len(out.get("diameter_middle_mm"))
    out["diameter_end"] = _conv_len(out.get("diameter_end_mm"))
    out["diameter_avg"] = _conv_len(out.get("diameter_avg_mm"))
    out["diameter_min"] = _conv_len(out.get("diameter_min_mm"))
    out["diameter_max"] = _conv_len(out.get("diameter_max_mm"))
    out["object_bbox_length"] = _conv_len(out.get("object_bbox_length_mm"))
    out["object_bbox_width"] = _conv_len(out.get("object_bbox_width_mm"))
    out["object_bbox_height"] = _conv_len(out.get("object_bbox_height_mm"))
    out["volume"] = _conv_vol(out.get("volume_m3"))
    out["object_bbox_volume"] = _conv_vol(out.get("object_bbox_volume_m3"))

    # Explicit per-field unit variants for Output Wizard frame builder.
    def _to_m(mm_val):
        if mm_val is None:
            return None
        try:
            return float(mm_val) / 1000.0
        except Exception:
            return None

    out["length_m"] = _to_m(out.get("length_mm"))
    out["diameter_start_m"] = _to_m(out.get("diameter_start_mm"))
    out["diameter_middle_m"] = _to_m(out.get("diameter_middle_mm"))
    out["diameter_end_m"] = _to_m(out.get("diameter_end_mm"))
    out["diameter_avg_m"] = _to_m(out.get("diameter_avg_mm"))
    out["diameter_min_m"] = _to_m(out.get("diameter_min_mm"))
    out["diameter_max_m"] = _to_m(out.get("diameter_max_mm"))
    out["object_bbox_length_m"] = _to_m(out.get("object_bbox_length_mm"))
    out["object_bbox_width_m"] = _to_m(out.get("object_bbox_width_mm"))
    out["object_bbox_height_m"] = _to_m(out.get("object_bbox_height_mm"))

    def _vol_m3(v):
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    vol = _vol_m3(out.get("volume_m3"))
    obj_vol = _vol_m3(out.get("object_bbox_volume_m3"))
    out["volume_l"] = None if vol is None else vol * 1000.0
    out["volume_mm3"] = None if vol is None else vol * 1_000_000_000.0
    out["object_bbox_volume_l"] = None if obj_vol is None else obj_vol * 1000.0
    out["object_bbox_volume_mm3"] = None if obj_vol is None else obj_vol * 1_000_000_000.0
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

    parts_meta: list[tuple[str, str]] = []
    frame_items = cfg.get("output_frame_items") or []
    if isinstance(frame_items, list) and len(frame_items) > 0:
        for item in frame_items:
            if not isinstance(item, dict):
                continue
            itype = str(item.get("type", "field") or "field").lower()
            if itype == "text":
                parts_meta.append(("text", str(item.get("text", "") or "")))
                continue
            if itype == "marker":
                parts_meta.append(("marker", str(item.get("value", "") or item.get("text", "") or "")))
                continue
            key = str(item.get("key", "") or "").strip()
            if not key:
                continue
            val = values.get(key)
            try:
                item_precision = int(item.get("precision", precision))
            except Exception:
                item_precision = precision
            item_precision = int(max(0, min(8, item_precision)))
            if isinstance(val, float):
                txt = f"{val:.{item_precision}f}"
            elif val is None:
                txt = ""
            else:
                txt = str(val)
            field_label = str(item.get("label", "") or key)
            parts_meta.append(("field", f"{field_label}={txt}" if include_labels else txt))
    else:
        keys = selected if selected else list(values.keys())
        for key in keys:
            val = values.get(key)
            if isinstance(val, float):
                txt = f"{val:.{precision}f}"
            elif val is None:
                txt = ""
            else:
                txt = str(val)
            parts_meta.append(("field", f"{key}={txt}" if include_labels else txt))

    body_parts: list[str] = []
    for idx, (ptype, txt) in enumerate(parts_meta):
        if idx > 0:
            prev_type = parts_meta[idx - 1][0]
            prev_is_leading_marker = (idx - 1 == 0) and (prev_type == "marker")
            curr_is_trailing_marker = (idx == len(parts_meta) - 1) and (ptype == "marker")
            if not (prev_is_leading_marker or curr_is_trailing_marker):
                body_parts.append(sep)
        body_parts.append(txt)
    return f"{prefix}{''.join(body_parts)}{suffix}"


@router.get("/analytics/output-preview")
async def analytics_output_preview(request: Request):
    """Return example output payload for current output settings and latest available analysis data."""
    session = _get_session(request)
    output_cfg = dict(device_manager.output_settings or {})

    analysis_cfg = device_manager.analysis_settings or {}
    analysis_app = str(session.get("analysis_app") or analysis_cfg.get("active_app", "log") or "log").strip().lower()
    if analysis_app not in {"log", "conveyor_object", "none"}:
        analysis_app = "log"
    metrics = session.get("analysis_metrics")
    source = "session"

    preview_session = dict(session)
    if not metrics:
        try:
            latest_list = list_measurements()
            if latest_list:
                latest_id = str((latest_list[0] or {}).get("id") or "")
                latest = get_measurement(latest_id) if latest_id else None
                if latest:
                    metrics = latest.get("metrics")
                    analysis_app = str(latest.get("analysis_app") or analysis_app)
                    preview_session["analysis_timestamp_ms"] = latest.get("created_at") or preview_session.get("analysis_timestamp_ms")
                    preview_session["distance_mm"] = latest.get("distance_mm") or preview_session.get("distance_mm")
                    preview_session["profiles_count"] = latest.get("profiles_count") or preview_session.get("profiles_count")
                    source = "history"
        except Exception:
            pass

    values = _flatten_output_values(preview_session, analysis_app, metrics)
    values = _apply_output_units(values, output_cfg)
    payload = _format_output_payload(values, output_cfg)
    return {
        "source": source,
        "analysis_app": analysis_app,
        "has_metrics": bool(metrics),
        "payload": payload,
        "timestamp_ms": values.get("timestamp_ms"),
    }


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
            if not bool(getattr(device, "enabled", True)):
                continue
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
        
        availability = _device_availability_summary(receiver_manager)
        return {
            "message": "Started listening for Picoscan UDP data",
            "results": results,
            "availability": {
                "enabled_total": availability["enabled_total"],
                "online_ids": availability["online_ids"],
                "offline_ids": availability["offline_ids"],
                "unknown_ids": availability["unknown_ids"],
            },
            "info": "Picoscan will send data to port 2115"
        }
    except Exception as e:
        logger.error(f"Error starting: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/trigger/start")
async def start_trigger(request: Request):
    """Start acquisition session (profile recording)."""
    receiver_manager = request.app.state.receiver_manager
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    # Ensure listeners exist for enabled devices before starting acquisition.
    for device in [d for d in device_manager.get_all_devices() if bool(getattr(d, "enabled", True))]:
        if device.device_id not in receiver_manager.receivers:
            receiver_manager.start_listening(
                device.device_id,
                "0.0.0.0",
                device.port,
                segments_per_scan=getattr(device, "segments_per_scan", None),
                format_type=getattr(device, "format_type", "compact"),
                device_type=getattr(device, "device_type", "picoscan"),
                sensor_ip=getattr(device, "ip_address", None),
            )

    availability = _device_availability_summary(receiver_manager)
    if availability["enabled_total"] <= 0:
        raise HTTPException(status_code=400, detail="No enabled devices configured")
    if len(availability["online_ids"]) == 0 and len(availability["unknown_ids"]) == 0:
        raise HTTPException(
            status_code=409,
            detail="Brak dostępnych urządzeń online. Oczekuj na auto-wznowienie nasłuchu lub sprawdź łączność.",
        )

    session = start_trigger_session(request.app)
    session["trigger_source"] = "manual"
    return {
        "recording": session.get("recording", False),
        "devices": session["devices"],
        "distance_mm": session["distance_mm"],
        "archive_pending": bool(session.get("archive_pending", False)),
        "archive_last_error": session.get("archive_last_error"),
        "availability": {
            "online_ids": availability["online_ids"],
            "offline_ids": availability["offline_ids"],
        },
    }


@router.post("/trigger/stop")
async def stop_trigger(request: Request):
    """Stop acquisition session."""
    session = stop_trigger_session(request.app)
    session["trigger_source"] = "manual"
    return {
        "recording": False,
        "distance_mm": session.get("distance_mm", 0.0),
        "analysis_timestamp_ms": session.get("analysis_timestamp_ms"),
        "analysis_duration_ms": session.get("analysis_duration_ms"),
        "archive_last_points_count": session.get("archive_last_points_count"),
        "archive_pending": bool(session.get("archive_pending", False)),
        "archive_last_error": session.get("archive_last_error"),
        "profiles_count": session.get("profiles_count", 0),
    }


@router.get("/analytics/results")
async def analytics_results(request: Request):
    session = _get_session(request)
    return {
        **_capture_quality(session),
        "analysis_app": session.get("analysis_app", (device_manager.analysis_settings or {}).get("active_app", "log")),
        "metrics": session.get("analysis_metrics"),
        "has_points": bool(session.get("analysis_points")),
        "analysis_duration_ms": session.get("analysis_duration_ms"),
        "analysis_timestamp_ms": session.get("analysis_timestamp_ms"),
    }


@router.post("/analytics/recompute")
async def analytics_recompute(request: Request):
    session = _get_session(request)
    if bool(session.get("recording", False)) or session.get("analysis_pending"):
        raise HTTPException(status_code=400, detail="Stop acquisition before recompute")
    _run_analysis_for_session(session, request.app)
    return {
        **_capture_quality(session),
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
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    _ensure_enabled_device_listeners(receiver_manager)
    availability = _device_availability_summary(receiver_manager)
    tdc_label = _resolve_tdc_label(request.app)
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
        "trigger_error": getattr(request.app.state, "tdc_trigger_error", None),
        "analysis_pending": bool(session.get("analysis_pending", False)),
        **_capture_quality(session),
        "trigger_source": session.get("trigger_source", "manual"),
        "devices_online": len(availability["online_ids"]),
        "devices_enabled": availability["enabled_total"],
        "archive_pending": bool(session.get("archive_pending", False)),
        "archive_last_error": session.get("archive_last_error"),
        "pipeline_timing_ms": _summarize_session_stage_timings(session),
    }


@router.get("/trigger/status/stream")
async def trigger_status_stream(request: Request):
    async def event_generator():
        while True:
            session = _get_session(request)
            _sync_encoder_worker(session)
            tdc_label = _resolve_tdc_label(request.app)
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
                "trigger_error": getattr(request.app.state, "tdc_trigger_error", None),
                "analysis_pending": bool(session.get("analysis_pending", False)),
                **_capture_quality(session),
                "trigger_source": session.get("trigger_source", "manual"),
                "archive_pending": bool(session.get("archive_pending", False)),
                "archive_last_error": session.get("archive_last_error"),
                "pipeline_timing_ms": _summarize_session_stage_timings(session),
            }
            payload.update(_capture_quality(session))
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/devices/availability")
async def devices_availability(request: Request):
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")
    _ensure_enabled_device_listeners(receiver_manager)
    summary = _device_availability_summary(receiver_manager)
    return {
        "enabled_total": summary["enabled_total"],
        "online_ids": summary["online_ids"],
        "offline_ids": summary["offline_ids"],
        "unknown_ids": summary["unknown_ids"],
        "health": summary["health"],
    }


@router.get("/trigger/performance-analysis")
async def trigger_performance_analysis(request: Request):
    session = _get_session(request)
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")
    return _estimate_speed_capability(session, receiver_manager)


@router.get("/trigger/latest-cloud")
async def trigger_latest_cloud(
    request: Request,
    max_points: int = 40000,
):
    session = _get_session(request)
    t_req = time.perf_counter()
    if max_points is not None and int(max_points) <= 0:
        full_np = _get_captured_full_points_np(session)
        points = full_np.tolist() if full_np.shape[0] > 0 else (session.get("last_points") or [])
        payload = {
            "points": points,
            "points_count": len(points),
            "total_points": len(points),
            "distance_mm": session.get("distance_mm", 0.0),
            "profiles_count": session.get("profiles_count", 0),
            "recording": session.get("recording", False),
            "preview_points_count": int(_get_captured_preview_points(session).shape[0]),
            "captured_points_count": int(session.get("captured_points_count", 0) or 0),
            "pipeline_timing_ms": _summarize_session_stage_timings(session),
        }
    else:
        preview_payload = _get_captured_preview_blob(session)
        preview_count = int(preview_payload.get("points_count", 0) or 0)
        total_points = preview_count
        if preview_count <= 0:
            points = session.get("last_points") or []
            total_points = len(points)
            if max_points is not None and max_points > 0 and total_points > max_points:
                t_downsample = time.perf_counter()
                step = max(1, total_points // max_points)
                points = points[::step]
                if len(points) > max_points:
                    points = points[:max_points]
                _record_session_stage(session, "latest_cloud_downsample", (time.perf_counter() - t_downsample) * 1000.0)
            payload = {
                "points": points,
                "points_count": len(points),
                "total_points": total_points,
                "distance_mm": session.get("distance_mm", 0.0),
                "profiles_count": session.get("profiles_count", 0),
                "recording": session.get("recording", False),
                "preview_points_count": int(_get_captured_preview_points(session).shape[0]),
                "captured_points_count": int(session.get("captured_points_count", 0) or 0),
                "pipeline_timing_ms": _summarize_session_stage_timings(session),
            }
        else:
            payload = {
                "points": [],
                "points_count": preview_count,
                "total_points": total_points,
                "distance_mm": session.get("distance_mm", 0.0),
                "profiles_count": session.get("profiles_count", 0),
                "recording": session.get("recording", False),
                "preview_points_count": int(_get_captured_preview_points(session).shape[0]),
                "captured_points_count": int(session.get("captured_points_count", 0) or 0),
                "pipeline_timing_ms": _summarize_session_stage_timings(session),
                **preview_payload,
            }
    _record_session_stage(session, "latest_cloud_request", (time.perf_counter() - t_req) * 1000.0)
    payload.update(_capture_quality(session))
    return payload


@router.get("/trigger/latest-cloud/stream")
async def trigger_latest_cloud_stream(
    request: Request,
    max_points: int = 30000,
):
    async def event_generator():
        while True:
            session = _get_session(request)
            t_req = time.perf_counter()
            preview_payload = _get_captured_preview_blob(session)
            total_points = int(preview_payload.get("points_count", 0) or 0)
            if total_points <= 0:
                points = session.get("last_points") or []
                total_points = len(points)
                if max_points is not None and max_points > 0 and total_points > max_points:
                    t_downsample = time.perf_counter()
                    step = max(1, total_points // max_points)
                    points = points[::step]
                    if len(points) > max_points:
                        points = points[:max_points]
                    _record_session_stage(session, "latest_cloud_stream_downsample", (time.perf_counter() - t_downsample) * 1000.0)
                payload = {
                    "points": points,
                    "points_count": len(points),
                    "total_points": total_points,
                    "recording": session.get("recording", False),
                    "pipeline_timing_ms": _summarize_session_stage_timings(session),
                }
            else:
                payload = {
                    "points": [],
                    "points_count": total_points,
                    "total_points": total_points,
                    "recording": session.get("recording", False),
                    "pipeline_timing_ms": _summarize_session_stage_timings(session),
                    **preview_payload,
                }
            _record_session_stage(session, "latest_cloud_stream_request", (time.perf_counter() - t_req) * 1000.0)
            payload.update(_capture_quality(session))
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


@router.get("/segment-stats/{device_id}")
async def get_segment_stats(device_id: str, request: Request):
    """Get lightweight diagnostics about the latest raw segment payload."""
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
            "stats": None,
            "info": "Receiver not initialized"
        }

    if not isinstance(receiver_info, dict):
        return {
            "device_id": device_id,
            "stats": None,
            "info": "Receiver entry not in dict form"
        }

    return {
        "device_id": device_id,
        "stats": receiver_info.get("last_segment_stats")
    }


@router.post("/test-receive/{device_id}")
async def test_receive(device_id: str, num_segments: int = 1):
    """Test receiving data from device with point cloud preview"""
    try:
        device = device_manager.get_device(device_id)
        if not device:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
        
        use_lmd_stream = _uses_lmd_stream(device)
        if use_lmd_stream:
            receiver = Lms4000Receiver(sensor_ip=device.ip_address, sensor_port=device.port)
            if not receiver.start_listening():
                raise HTTPException(status_code=400, detail=f"Failed to connect LMDscandata stream {device.ip_address}:{device.port}")
            points = receiver.receive_point_cloud(max(1, num_segments))
            receiver.stop_listening()
            if points is None or len(points) == 0:
                fallback_points, fallback_meta = _try_scansegment_direct_udp(device, num_segments)
                if fallback_points is not None and len(fallback_points) > 0:
                    points = fallback_points
                    if hasattr(points, 'tolist'):
                        points_list = points.tolist()
                    else:
                        points_list = list(points)
                    return {
                        "device_id": device_id,
                        "status": "success_with_fallback",
                        "source": fallback_meta,
                        "points_received": len(points_list),
                        "port": fallback_meta.get("listen_port"),
                        "points": points_list,
                    }
                return {
                    "device_id": device_id,
                    "status": "connected_but_no_data",
                    "message": f"Connected to {device.ip_address}:{device.port}, but no LMDscandata received",
                    "hint": "Try ScanSegmentAPI UDP path (compact/msgpack) and verify scanner ScanDataEthSettings target IP/port.",
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
            receiver_info = receiver_manager.receivers.get(device_id)
            if not receiver_info:
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
                    yield f"data: {json.dumps({'error': 'Failed to start live preview listener for ' + str(device.device_id)})}\n\n"
                    return

            try:
                logger.info(f"Live preview started for {device_id}")
                frame_count = 0
                while True:
                    try:
                        latest = receiver_manager.get_latest_point_cloud(device_id)
                        if not latest or latest.get("points") is None or len(latest.get("points")) == 0:
                            yield f"data: {json.dumps({'status': 'waiting', 'device_id': device_id})}\n\n"
                            await asyncio.sleep(0.05)
                            continue
                        points = latest["points"]
                        frame_number = latest.get("frame")

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
        
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
