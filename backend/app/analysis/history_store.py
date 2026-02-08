from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

HISTORY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "data", "history")


def _ensure_dir() -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)


def _history_files() -> List[str]:
    _ensure_dir()
    files = [f for f in os.listdir(HISTORY_DIR) if f.endswith(".json")]
    files.sort()
    return [os.path.join(HISTORY_DIR, f) for f in files]


def save_measurement(payload: Dict[str, Any], keep_last: int = 10) -> Dict[str, Any]:
    _ensure_dir()
    ts = int(time.time() * 1000)
    meas_id = f"{ts}"
    record = {
        "id": meas_id,
        "created_at": ts,
        **payload,
    }
    path = os.path.join(HISTORY_DIR, f"{meas_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f)

    # prune old
    files = _history_files()
    if len(files) > keep_last:
        for old in files[: len(files) - keep_last]:
            try:
                os.remove(old)
            except OSError:
                pass

    return record


def list_measurements() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in _history_files():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items.append({
                "id": data.get("id"),
                "created_at": data.get("created_at"),
                "profiles_count": data.get("profiles_count"),
                "distance_mm": data.get("distance_mm"),
                "points_count": len(data.get("original_points") or []),
                "augmented_points_count": len(data.get("augmented_points") or []),
                "analysis_duration_ms": data.get("analysis_duration_ms"),
                "metrics": data.get("metrics"),
            })
        except Exception:
            continue
    # newest first
    items.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return items


def get_measurement(meas_id: str) -> Dict[str, Any] | None:
    path = os.path.join(HISTORY_DIR, f"{meas_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
