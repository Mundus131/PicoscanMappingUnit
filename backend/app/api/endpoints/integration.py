from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.device_manager import device_manager
from app.services.integration_mapper import (
    DEFAULT_NAMESPACE_URI,
    DEFAULT_TOPIC_ROOT,
    build_active_bundle,
    build_catalog,
    build_snapshot,
)
from app.api.endpoints.acquisition import (
    _device_availability_summary,
    _ensure_enabled_device_listeners,
    _get_session,
    _resolve_tdc_label,
)


router = APIRouter()


def _normalized_active_app() -> str:
    active = str((device_manager.analysis_settings or {}).get("active_app", "log") or "log").strip().lower()
    if active not in {"log", "conveyor_object", "none"}:
        active = "log"
    return active


@router.get("/catalog")
async def integration_catalog(
    topic_root: str = Query(DEFAULT_TOPIC_ROOT),
    namespace_uri: str = Query(DEFAULT_NAMESPACE_URI),
):
    return build_catalog(topic_root=topic_root, namespace_uri=namespace_uri)


@router.get("/snapshot")
async def integration_snapshot(
    request: Request,
    app: str = Query("active"),
    topic_root: str = Query(DEFAULT_TOPIC_ROOT),
    namespace_uri: str = Query(DEFAULT_NAMESPACE_URI),
):
    session = _get_session(request)
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")
    _ensure_enabled_device_listeners(receiver_manager)
    availability = _device_availability_summary(receiver_manager)
    tdc_label = _resolve_tdc_label(request.app)
    selected = str(app or "active").strip().lower()
    if selected == "active":
        selected = _normalized_active_app()
        if selected == "none":
            selected = "diagnostics"
    return build_snapshot(
        selected,
        session,
        availability,
        tdc_label,
        topic_root=topic_root,
        namespace_uri=namespace_uri,
    )


@router.get("/active-bundle")
async def integration_active_bundle(
    request: Request,
    topic_root: str = Query(DEFAULT_TOPIC_ROOT),
    namespace_uri: str = Query(DEFAULT_NAMESPACE_URI),
):
    session = _get_session(request)
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")
    _ensure_enabled_device_listeners(receiver_manager)
    availability = _device_availability_summary(receiver_manager)
    tdc_label = _resolve_tdc_label(request.app)
    return build_active_bundle(
        session,
        availability,
        tdc_label,
        active_app=_normalized_active_app(),
        topic_root=topic_root,
        namespace_uri=namespace_uri,
    )


@router.get("/status")
async def integration_status(request: Request):
    session = _get_session(request)
    receiver_manager = getattr(request.app.state, "receiver_manager", None)
    if receiver_manager is None:
        raise HTTPException(status_code=500, detail="Receiver manager not initialized")
    _ensure_enabled_device_listeners(receiver_manager)
    availability = _device_availability_summary(receiver_manager)
    tdc_label = _resolve_tdc_label(request.app)
    bundle = build_active_bundle(
        session,
        availability,
        tdc_label,
        active_app=_normalized_active_app(),
        topic_root=DEFAULT_TOPIC_ROOT,
        namespace_uri=DEFAULT_NAMESPACE_URI,
    )
    mqtt_publisher = getattr(request.app.state, "mqtt_publisher", None)
    mqtt_status = (
        mqtt_publisher.get_status()
        if mqtt_publisher and hasattr(mqtt_publisher, "get_status")
        else {
            "enabled": False,
            "connected": False,
            "broker_reachable": False,
            "last_publish_ts_ms": None,
            "last_error": "publisher_not_initialized",
        }
    )
    catalog = build_catalog()
    return {
        "active_app": bundle.get("active_app"),
        "mqtt": mqtt_status,
        "topics": [item.get("topic") for item in (bundle.get("mqtt_messages") or []) if item.get("topic")],
        "catalog": catalog.get("applications") or [],
    }
