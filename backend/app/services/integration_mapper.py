from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np


DEFAULT_TOPIC_ROOT = "picocan/pmu"
DEFAULT_NAMESPACE_URI = "urn:picocan:mapping-unit"


CATALOG = {
    "log": {
        "description": "Measurement result for cylindrical/log analysis.",
        "mqtt_topic": f"{DEFAULT_TOPIC_ROOT}/log/result",
        "mqtt_fields": [
            "result_ready",
            "timestamp_ms",
            "timestamp_iso",
            "distance_mm",
            "profiles_count",
            "volume_mm3",
            "volume_m3",
            "length_mm",
            "diameter_start_mm",
            "diameter_middle_mm",
            "diameter_end_mm",
            "diameter_avg_mm",
            "diameter_min_mm",
            "diameter_max_mm",
            "analysis_duration_ms",
        ],
        "opcua_nodes": [
            "Applications.Log.ResultReady",
            "Applications.Log.TimestampMs",
            "Applications.Log.DistanceMm",
            "Applications.Log.ProfilesCount",
            "Applications.Log.VolumeMm3",
            "Applications.Log.VolumeM3",
            "Applications.Log.LengthMm",
            "Applications.Log.DiameterAvgMm",
            "Applications.Log.DiameterMinMm",
            "Applications.Log.DiameterMaxMm",
        ],
    },
    "conveyor_object": {
        "description": "Object-above-conveyor result payload.",
        "mqtt_topic": f"{DEFAULT_TOPIC_ROOT}/conveyor_object/result",
        "mqtt_fields": [
            "result_ready",
            "timestamp_ms",
            "timestamp_iso",
            "distance_mm",
            "profiles_count",
            "object_points_count",
            "centroid_mm",
            "bbox_mm",
            "bbox_volume_mm3",
            "bbox_volume_m3",
            "height_above_plane_mm",
            "top_plane",
            "analysis_duration_ms",
        ],
        "opcua_nodes": [
            "Applications.ConveyorObject.ResultReady",
            "Applications.ConveyorObject.TimestampMs",
            "Applications.ConveyorObject.DistanceMm",
            "Applications.ConveyorObject.ProfilesCount",
            "Applications.ConveyorObject.ObjectPointsCount",
            "Applications.ConveyorObject.Centroid.XMm",
            "Applications.ConveyorObject.Centroid.YMm",
            "Applications.ConveyorObject.Centroid.ZMm",
            "Applications.ConveyorObject.BBox.LengthMm",
            "Applications.ConveyorObject.BBox.WidthMm",
            "Applications.ConveyorObject.BBox.HeightMm",
            "Applications.ConveyorObject.BBox.VolumeMm3",
        ],
    },
    "diagnostics": {
        "description": "Runtime and receiver health diagnostics.",
        "mqtt_topic": f"{DEFAULT_TOPIC_ROOT}/diagnostics/status",
        "mqtt_fields": [
            "timestamp_ms",
            "timestamp_iso",
            "recording",
            "active_app",
            "devices_enabled",
            "devices_online",
            "tdc_input_state",
            "trigger_source",
            "speed_mps",
            "encoder_rpm",
            "encoder_speed_mps",
            "profiles_count",
            "last_preview_points_count",
            "captured_points_count",
            "archive_pending",
            "device_health",
        ],
        "opcua_nodes": [
            "Diagnostics.Recording",
            "Diagnostics.ActiveApp",
            "Diagnostics.DevicesEnabled",
            "Diagnostics.DevicesOnline",
            "Diagnostics.TdcInputState",
            "Diagnostics.TriggerSource",
            "Diagnostics.SpeedMps",
            "Diagnostics.ProfilesCount",
            "Diagnostics.CapturedPointsCount",
            "Diagnostics.ArchivePending",
        ],
    },
}


def _sanitize_node_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", str(value or "").strip()) or "Value"


def _scalar_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "Int64"
    if isinstance(value, float):
        return "Double"
    return "String"


def _timestamp_fields(ts_ms: int | None) -> dict[str, Any]:
    ts = int(ts_ms or int(time.time() * 1000))
    return {
        "timestamp_ms": ts,
        "timestamp_iso": datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat(),
    }


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _compact_device_health(availability: dict[str, Any]) -> dict[str, Any]:
    health = availability.get("health") or {}
    out: dict[str, Any] = {}
    for device_id, item in health.items():
        if not isinstance(item, dict):
            continue
        node = _sanitize_node_name(device_id)
        out[node] = {
            "availability": item.get("availability"),
            "data_rate_hz": _safe_float(item.get("data_rate_hz")),
            "latest_data_age_s": _safe_float(item.get("latest_data_age_s")),
            "last_points_count": _safe_int(item.get("last_points_count")),
            "expected_segment_pattern": list(item.get("expected_segment_pattern") or []),
            "last_complete_segment_pattern": list(item.get("last_complete_segment_pattern") or []),
            "incomplete_frames_dropped": _safe_int(item.get("incomplete_frames_dropped")),
        }
    return out


def _build_log_payload(session: dict[str, Any]) -> dict[str, Any]:
    metrics = session.get("analysis_metrics") or {}
    slices = metrics.get("slices") or []
    first = slices[0] if slices else {}
    last = slices[-1] if slices else {}
    diam = metrics.get("diameter_mm") or {}
    payload = {
        **_timestamp_fields(session.get("analysis_timestamp_ms")),
        "application": "log",
        "result_ready": bool(metrics),
        "distance_mm": _safe_float(session.get("distance_mm")) or 0.0,
        "profiles_count": _safe_int(session.get("profiles_count")) or 0,
        "analysis_duration_ms": _safe_int(session.get("analysis_duration_ms")),
        "volume_mm3": _safe_float(metrics.get("volume_mm3")),
        "volume_m3": _safe_float(metrics.get("volume_m3")),
        "length_mm": _safe_float(metrics.get("total_length_mm")),
        "diameter_start_mm": _safe_float(first.get("diameter_mm")),
        "diameter_middle_mm": _safe_float(diam.get("middle")),
        "diameter_end_mm": _safe_float(last.get("diameter_mm")),
        "diameter_avg_mm": _safe_float(diam.get("avg")),
        "diameter_min_mm": _safe_float(diam.get("min")),
        "diameter_max_mm": _safe_float(diam.get("max")),
    }
    return payload


def _build_conveyor_payload(session: dict[str, Any]) -> dict[str, Any]:
    metrics = session.get("analysis_metrics") or {}
    obj = metrics.get("object") or {}
    bbox = obj.get("bbox_mm") or {}
    centroid = obj.get("centroid_mm") or [None, None, None]
    heights = obj.get("height_above_plane_mm") or {}
    top_plane = obj.get("top_plane") or {}
    payload = {
        **_timestamp_fields(session.get("analysis_timestamp_ms")),
        "application": "conveyor_object",
        "result_ready": bool(metrics),
        "distance_mm": _safe_float(session.get("distance_mm")) or 0.0,
        "profiles_count": _safe_int(session.get("profiles_count")) or 0,
        "analysis_duration_ms": _safe_int(session.get("analysis_duration_ms")),
        "object_points_count": _safe_int(obj.get("points_count")),
        "centroid_mm": {
            "x": _safe_float(centroid[0] if len(centroid) > 0 else None),
            "y": _safe_float(centroid[1] if len(centroid) > 1 else None),
            "z": _safe_float(centroid[2] if len(centroid) > 2 else None),
        },
        "bbox_mm": {
            "length": _safe_float(bbox.get("length")),
            "width": _safe_float(bbox.get("width")),
            "height": _safe_float(bbox.get("height")),
        },
        "bbox_volume_mm3": _safe_float(obj.get("bbox_volume_mm3")),
        "bbox_volume_m3": _safe_float(obj.get("bbox_volume_m3")),
        "height_above_plane_mm": {
            "min": _safe_float(heights.get("min")),
            "max": _safe_float(heights.get("max")),
            "avg": _safe_float(heights.get("avg")),
        },
        "top_plane": {
            "points_count": _safe_int(top_plane.get("points_count")),
            "height_avg_mm": _safe_float(top_plane.get("height_avg_mm")),
            "height_min_mm": _safe_float(top_plane.get("height_min_mm")),
            "height_max_mm": _safe_float(top_plane.get("height_max_mm")),
            "footprint_angle_deg": _safe_float(top_plane.get("footprint_angle_deg")),
        },
    }
    return payload


def _build_diagnostics_payload(
    session: dict[str, Any],
    availability: dict[str, Any],
    tdc_input_state: str,
) -> dict[str, Any]:
    active_app = str(session.get("analysis_app") or "none")
    return {
        **_timestamp_fields(session.get("analysis_timestamp_ms")),
        "application": "diagnostics",
        "recording": bool(session.get("recording", False)),
        "active_app": active_app,
        "devices_enabled": int(availability.get("enabled_total", 0) or 0),
        "devices_online": int(len(availability.get("online_ids") or [])),
        "devices_offline": int(len(availability.get("offline_ids") or [])),
        "tdc_input_state": tdc_input_state,
        "trigger_source": str(session.get("trigger_source") or "manual"),
        "speed_mps": _safe_float(session.get("speed_mps")),
        "encoder_rpm": _safe_float(session.get("encoder_rpm")),
        "encoder_speed_mps": _safe_float(session.get("encoder_speed_mps")),
        "profiling_distance_mm": _safe_float(session.get("profiling_distance_mm")),
        "profiles_count": _safe_int(session.get("profiles_count")) or 0,
        "last_preview_points_count": _safe_int(len(session.get("last_points") or [])) or 0,
        "captured_points_count": _safe_int(session.get("captured_points_count")) or 0,
        "archive_pending": bool(session.get("archive_pending", False)),
        "archive_last_error": session.get("archive_last_error"),
        "analysis_duration_ms": _safe_int(session.get("analysis_duration_ms")),
        "device_health": _compact_device_health(availability),
    }


def _payload_for_app(
    app_name: str,
    session: dict[str, Any],
    availability: dict[str, Any],
    tdc_input_state: str,
) -> dict[str, Any]:
    if app_name == "log":
        return _build_log_payload(session)
    if app_name == "conveyor_object":
        return _build_conveyor_payload(session)
    return _build_diagnostics_payload(session, availability, tdc_input_state)


def _flatten_opcua_nodes(prefix: str, value: Any) -> dict[str, dict[str, Any]]:
    flat: dict[str, dict[str, Any]] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{_sanitize_node_name(key)}" if prefix else _sanitize_node_name(key)
            flat.update(_flatten_opcua_nodes(child_prefix, child))
        return flat
    if isinstance(value, (list, tuple)):
        text = json.dumps(value, ensure_ascii=True)
        flat[prefix] = {"value": text, "data_type": "String"}
        return flat
    if value is None:
        return flat
    flat[prefix] = {"value": value, "data_type": _scalar_type_name(value)}
    return flat


def _flatten_mqtt_messages(base_topic: str, value: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_topic = f"{base_topic}/{str(key).strip()}"
            messages.extend(_flatten_mqtt_messages(child_topic, child))
        return messages
    if isinstance(value, (list, tuple)):
        messages.append(
            {
                "topic": base_topic,
                "qos": 1,
                "retain": True,
                "payload": list(value),
            }
        )
        return messages
    if value is None:
        messages.append(
            {
                "topic": base_topic,
                "qos": 1,
                "retain": True,
                "payload": None,
            }
        )
        return messages
    messages.append(
        {
            "topic": base_topic,
            "qos": 1,
            "retain": True,
            "payload": value,
        }
    )
    return messages


def build_snapshot(
    app_name: str,
    session: dict[str, Any],
    availability: dict[str, Any],
    tdc_input_state: str,
    topic_root: str = DEFAULT_TOPIC_ROOT,
    namespace_uri: str = DEFAULT_NAMESPACE_URI,
) -> dict[str, Any]:
    normalized_app = str(app_name or "diagnostics").strip().lower()
    if normalized_app not in {"log", "conveyor_object", "diagnostics"}:
        normalized_app = "diagnostics"
    payload = _payload_for_app(normalized_app, session, availability, tdc_input_state)
    mqtt_topic = f"{str(topic_root or DEFAULT_TOPIC_ROOT).rstrip('/')}/{normalized_app}/{'status' if normalized_app == 'diagnostics' else 'result'}"
    node_root = {
        "log": "Applications.Log",
        "conveyor_object": "Applications.ConveyorObject",
        "diagnostics": "Diagnostics",
    }[normalized_app]
    opcua_nodes = _flatten_opcua_nodes(node_root, payload)
    mqtt_messages = _flatten_mqtt_messages(mqtt_topic, payload)
    return {
        "application": normalized_app,
        "generated_at_ms": int(time.time() * 1000),
        "mqtt": {
            "topic": mqtt_topic,
            "messages": mqtt_messages,
        },
        "opcua": {
            "namespace_uri": namespace_uri or DEFAULT_NAMESPACE_URI,
            "root": node_root,
            "nodes": opcua_nodes,
        },
        "payload": payload,
    }


def build_active_bundle(
    session: dict[str, Any],
    availability: dict[str, Any],
    tdc_input_state: str,
    active_app: str,
    topic_root: str = DEFAULT_TOPIC_ROOT,
    namespace_uri: str = DEFAULT_NAMESPACE_URI,
) -> dict[str, Any]:
    chosen = str(active_app or "none").strip().lower()
    if chosen not in {"log", "conveyor_object"}:
        chosen = "none"
    snapshots: list[dict[str, Any]] = [
        build_snapshot("diagnostics", session, availability, tdc_input_state, topic_root, namespace_uri)
    ]
    if chosen != "none":
        snapshots.insert(0, build_snapshot(chosen, session, availability, tdc_input_state, topic_root, namespace_uri))

    mqtt_messages: list[dict[str, Any]] = []
    for snap in snapshots:
        mqtt_messages.extend(list((snap.get("mqtt") or {}).get("messages") or []))
    opcua_nodes: dict[str, dict[str, Any]] = {}
    for snap in snapshots:
        opcua_nodes.update(snap["opcua"]["nodes"])

    return {
        "generated_at_ms": int(time.time() * 1000),
        "active_app": chosen,
        "topic_root": topic_root or DEFAULT_TOPIC_ROOT,
        "namespace_uri": namespace_uri or DEFAULT_NAMESPACE_URI,
        "snapshots": snapshots,
        "mqtt_messages": mqtt_messages,
        "opcua_nodes": opcua_nodes,
    }


def build_catalog(topic_root: str = DEFAULT_TOPIC_ROOT, namespace_uri: str = DEFAULT_NAMESPACE_URI) -> dict[str, Any]:
    apps = []
    for app_name, meta in CATALOG.items():
        apps.append(
            {
                "application": app_name,
                "description": meta["description"],
                "mqtt_topic": f"{str(topic_root or DEFAULT_TOPIC_ROOT).rstrip('/')}/{app_name}/{'status' if app_name == 'diagnostics' else 'result'}",
                "mqtt_fields": meta["mqtt_fields"],
                "opcua_namespace_uri": namespace_uri or DEFAULT_NAMESPACE_URI,
                "opcua_nodes": meta["opcua_nodes"],
            }
        )
    return {
        "topic_root": topic_root or DEFAULT_TOPIC_ROOT,
        "namespace_uri": namespace_uri or DEFAULT_NAMESPACE_URI,
        "applications": apps,
    }
