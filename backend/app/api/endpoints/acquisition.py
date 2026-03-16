from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import List, Generator
from pydantic import BaseModel
from app.core.device_manager import device_manager
from app.analysis.log_measurement import compute_log_metrics, build_augmented_cloud
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
from collections import deque
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
            "preview_points_history": deque(maxlen=30),
            "profile_frame_buffer": [],
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
            "accumulated_chunks": [],
            "accumulated_points_count": 0,
            "last_points_np": None,
            "last_points_emit_ts": 0.0,
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
    session.setdefault("accumulated_chunks", [])
    session.setdefault("accumulated_points_count", 0)
    session.setdefault("last_points_np", None)
    session.setdefault("last_points_emit_ts", 0.0)
    session.setdefault("preview_points_history", deque(maxlen=30))
    session.setdefault("profile_frame_buffer", [])
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

    t_loop_start = time.perf_counter()
    profiles_added_in_cycle = 0
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
            session["last_points_np"] = np.asarray(pts, dtype=np.float32)
            # Avoid expensive list conversion on every loop iteration.
            # UI does not need 20Hz full serialization.
            now_emit = time.time()
            last_emit = float(session.get("last_points_emit_ts", 0.0) or 0.0)
            if (now_emit - last_emit) >= 0.15:
                session["last_points"] = pts.tolist()
                session["last_points_emit_ts"] = now_emit
                history = session.get("preview_points_history")
                if isinstance(history, deque):
                    history.append(np.array(pts, copy=True))
            # Buffer frames for profile accumulation (used when recording).
            buffer = session.get("profile_frame_buffer")
            if session.get("recording") and isinstance(buffer, list):
                buffer.append(np.array(pts, copy=True))

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
                    profiles_added_in_cycle = profiles_to_add
                    # Use accumulated frames since last profile to build a denser profile.
                    buffer = session.get("profile_frame_buffer")
                    if isinstance(buffer, list) and len(buffer) > 0:
                        try:
                            profile_points = np.vstack(buffer).astype(np.float32)
                        except Exception:
                            profile_points = np.asarray(pts, dtype=np.float32)
                        buffer.clear()
                    else:
                        profile_points = np.asarray(pts, dtype=np.float32)
                    chunks = session.get("accumulated_chunks")
                    if not isinstance(chunks, list):
                        chunks = []
                        session["accumulated_chunks"] = chunks
                    if profiles_to_add == 1:
                        step_distance = last_profile_distance + profiling_distance_mm
                        if profile_points.shape[1] >= 3:
                            prof = profile_points.copy()
                            prof[:, 1] = step_distance
                        else:
                            prof = profile_points
                        chunks.append(np.asarray(prof, dtype=np.float32))
                        session["accumulated_points_count"] = int(
                            session.get("accumulated_points_count", 0) + int(prof.shape[0])
                        )
                    else:
                        for i in range(profiles_to_add):
                            step_distance = last_profile_distance + profiling_distance_mm * (i + 1)
                            if profile_points.shape[1] >= 3:
                                prof = profile_points.copy()
                                # Use data Y as motion axis (matches x/z scan plane in UI)
                                prof[:, 1] = step_distance
                            else:
                                prof = profile_points
                            chunks.append(np.asarray(prof, dtype=np.float32))
                            session["accumulated_points_count"] = int(
                                session.get("accumulated_points_count", 0) + int(prof.shape[0])
                            )
                    session["profiles_count"] = int(session.get("profiles_count", 0) + profiles_to_add)
                    # Keep full acquisition points for analysis fidelity.
                    # Visualization endpoints apply their own max_points downsampling.
                    session["last_profile_distance_mm"] = last_profile_distance + profiling_distance_mm * profiles_to_add
    except Exception:
        pass
    finally:
        loop_ms = (time.perf_counter() - t_loop_start) * 1000.0
        perf_loop = session.get("perf_loop_ms")
        if not isinstance(perf_loop, list):
            perf_loop = []
            session["perf_loop_ms"] = perf_loop
        perf_loop.append(loop_ms)
        if len(perf_loop) > 400:
            del perf_loop[:-400]
        session["perf_total_updates"] = int(session.get("perf_total_updates", 0) + 1)
        session["perf_last_profiles_added"] = int(profiles_added_in_cycle)
        session["perf_max_profiles_per_cycle"] = int(
            max(int(session.get("perf_max_profiles_per_cycle", 0) or 0), int(profiles_added_in_cycle))
        )


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


def _materialize_accumulated_points(session: dict):
    chunks = session.get("accumulated_chunks")
    if isinstance(chunks, list) and chunks:
        arrays = [c for c in chunks if isinstance(c, np.ndarray) and c.size > 0]
        if arrays:
            try:
                merged = np.vstack(arrays)
                session["accumulated_points"] = merged.tolist()
            except Exception:
                flat = []
                for arr in arrays:
                    flat.extend(arr.tolist())
                session["accumulated_points"] = flat
        else:
            session["accumulated_points"] = []
    else:
        session["accumulated_points"] = session.get("accumulated_points") or []


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
    session["accumulated_chunks"] = []
    session["accumulated_points_count"] = 0
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
    session["last_points_np"] = None
    session["last_points_emit_ts"] = 0.0
    session["start_delay_mm_remaining"] = 0.0
    session["devices"] = [d.device_id for d in device_manager.get_all_devices() if d.enabled]
    _sync_encoder_worker(session)
    _update_session_once(session, app.state.receiver_manager)
    return session


def _run_analysis_for_session(session: dict, app):
    try:
        t0 = time.time()
        _materialize_accumulated_points(session)
        points = session.get("accumulated_points") or []
        # If no accumulated profiles were created (e.g. encoder mode without enough
        # movement), use latest cloud as fallback so analysis reflects current run.
        if not points:
            latest = session.get("last_points") or []
            if latest:
                points = latest
                session["profiles_count"] = max(1, int(session.get("profiles_count", 0) or 0))
        profiling_distance_mm = session.get("profiling_distance_mm") or 10.0
        analysis_cfg = device_manager.analysis_settings or {}
        analysis_app = str(analysis_cfg.get("active_app", "log") or "log").strip().lower()
        if analysis_app not in {"log", "none"}:
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
            else:
                # "none" mode: keep only acquisition cloud, skip metric analysis.
                session["analysis_metrics"] = None
                session["analysis_points"] = []
                metrics_for_save = None
            session["analysis_duration_ms"] = int((time.time() - t0) * 1000)
            try:
                notifier = app.state.tcp_notifier
            except Exception:
                notifier = None
            output_cfg = dict(device_manager.output_settings or {})
            if notifier and bool(output_cfg.get("enabled", False)) and analysis_app != "none":
                values = _flatten_output_values(session, analysis_app, metrics_for_save or session.get("analysis_metrics"))
                values = _apply_output_units(values, output_cfg)
                payload = _format_output_payload(values, output_cfg)
                notifier.broadcast(payload)
            try:
                t_archive_start = time.perf_counter()
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
                session["archive_last_duration_ms"] = int((time.perf_counter() - t_archive_start) * 1000)
                session["archive_last_points_count"] = int(len(points))
                session["archive_last_ts"] = int(time.time() * 1000)
                logger.info(
                    "Analysis archived: app=%s points=%s profiles=%s duration_ms=%s",
                    analysis_app,
                    int(len(points)),
                    int(session.get("profiles_count", 0) or 0),
                    session.get("analysis_duration_ms"),
                )
            except Exception:
                pass
        else:
            session["analysis_metrics"] = None
            session["analysis_points"] = []
            session["analysis_duration_ms"] = None
            session["analysis_timestamp_ms"] = None
            logger.info("Analysis skipped: no points available for current session")
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
    # Try one final data pull while still recording to reduce chance of missing
    # the last profile at stop edge.
    try:
        _update_session_once(session, app.state.receiver_manager)
    except Exception:
        pass
    session["recording"] = False
    _sync_encoder_worker(session)
    stop_event = session.get("worker_stop_event")
    if stop_event:
        stop_event.set()
    _run_analysis_for_session(session, app)
    # Release chunk buffers after analysis materialization to reduce memory pressure.
    session["accumulated_chunks"] = []
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
        },
        "device_availability": {
            "enabled_total": enabled_total,
            "online": online_count,
            "online_ratio": online_ratio,
            "online_ids": availability["online_ids"],
            "offline_ids": availability["offline_ids"],
        },
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
    if analysis_app not in {"log", "none"}:
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
        "profiles_count": session.get("profiles_count", 0),
    }


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
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    _ensure_enabled_device_listeners(receiver_manager)
    availability = _device_availability_summary(receiver_manager)
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
        "devices_online": len(availability["online_ids"]),
        "devices_enabled": availability["enabled_total"],
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


@router.post("/segments/estimate/{device_id}")
async def estimate_device_segments(
    device_id: str,
    request: Request,
    sample_seconds: float = 3.0,
    min_samples: int = 6,
    auto_apply: bool = False,
):
    """
    Estimate segments_per_scan before regular acquisition.
    Uses runtime frame/segment counters observed by receiver workers.
    """
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")

    device = device_manager.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    if not bool(getattr(device, "enabled", True)):
        raise HTTPException(status_code=400, detail=f"Device {device_id} is disabled")

    # Ensure listener exists.
    if device_id not in receiver_manager.receivers:
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
                status_code=400,
                detail=f"Failed to start listener for {device_id} on {device.ip_address}:{device.port}",
            )

    # LMDscandata path: segment count estimation is not meaningful for this stream type.
    if _uses_lmd_stream(device):
        return {
            "device_id": device_id,
            "device_type": str(getattr(device, "device_type", "picoscan") or "picoscan").lower(),
            "format_type": "lmdscandata",
            "segments_per_scan_estimated": None,
            "samples": 0,
            "applied": False,
            "note": "Segmentation estimate is not applicable for LMDscandata stream.",
        }

    deadline = time.time() + max(1.0, float(sample_seconds))
    best_estimate = None
    best_samples = 0
    health_item = None
    while time.time() < deadline:
        health = receiver_manager.get_health_snapshot()
        health_item = health.get(device_id) or {}
        estimate = health_item.get("segments_per_scan_estimated")
        samples = int(health_item.get("segments_estimate_samples") or 0)
        if estimate:
            best_estimate = int(estimate)
            best_samples = max(best_samples, samples)
        if best_estimate and best_samples >= max(1, int(min_samples)):
            break
        time.sleep(0.1)

    applied = False
    if auto_apply and best_estimate and best_estimate > 0:
        device_manager.update_device(device_id, {"device_id": device_id, "segments_per_scan": int(best_estimate)})
        applied = True

    if not best_estimate:
        return {
            "device_id": device_id,
            "segments_per_scan_estimated": None,
            "samples": best_samples,
            "applied": False,
            "health": health_item or {},
            "note": "No estimate yet. Check UDP stream and try with longer sample_seconds.",
        }

    return {
        "device_id": device_id,
        "segments_per_scan_estimated": int(best_estimate),
        "samples": best_samples,
        "configured_segments_per_scan": getattr(device_manager.get_device(device_id), "segments_per_scan", None),
        "applied": applied,
        "health": health_item or {},
    }


@router.get("/trigger/latest-cloud")
async def trigger_latest_cloud(
    request: Request,
    max_points: int = 40000,
    accumulate_frames: int = 1,
    accumulate_profiles: int = 0,
):
    session = _get_session(request)
    points = None
    chunks = session.get("accumulated_chunks")
    if isinstance(chunks, list) and len(chunks) > 0:
        try:
            points = np.vstack(chunks).tolist() if len(chunks) > 1 else chunks[0].tolist()
        except Exception:
            points = None
    if points is None and session.get("accumulated_points"):
        points = session.get("accumulated_points") or None
    if int(accumulate_frames or 1) > 1:
        history = session.get("preview_points_history")
        if isinstance(history, deque) and len(history) > 0:
            take = min(int(accumulate_frames), len(history))
            recent = list(history)[-take:]
            try:
                points = np.vstack(recent).tolist() if len(recent) > 1 else recent[0].tolist()
            except Exception:
                points = None
    if points is None:
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
async def trigger_latest_cloud_stream(
    request: Request,
    max_points: int = 30000,
    accumulate_frames: int = 1,
    accumulate_profiles: int = 0,
):
    async def event_generator():
        while True:
            session = _get_session(request)
            points = None
            chunks = session.get("accumulated_chunks")
            if isinstance(chunks, list) and len(chunks) > 0:
                try:
                    points = np.vstack(chunks).tolist() if len(chunks) > 1 else chunks[0].tolist()
                except Exception:
                    points = None
            if points is None and session.get("accumulated_points"):
                points = session.get("accumulated_points") or None
            if int(accumulate_frames or 1) > 1:
                history = session.get("preview_points_history")
                if isinstance(history, deque) and len(history) > 0:
                    take = min(int(accumulate_frames), len(history))
                    recent = list(history)[-take:]
                    try:
                        points = np.vstack(recent).tolist() if len(recent) > 1 else recent[0].tolist()
                    except Exception:
                        points = None
            if points is None:
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
            # Check if receiver already exists (from auto-start)
            receiver_info = receiver_manager.receivers.get(device_id)
            
            use_lmd_stream = _uses_lmd_stream(device)
            if not receiver_info:
                # No active receiver - create temporary one
                if use_lmd_stream:
                    receiver = Lms4000Receiver(sensor_ip=device.ip_address, sensor_port=device.port)
                    if not receiver.start_listening():
                        yield f"data: {json.dumps({'error': 'Failed to connect LMDscandata stream ' + str(device.ip_address) + ':' + str(device.port)})}\n\n"
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
                        elif use_lmd_stream:
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
