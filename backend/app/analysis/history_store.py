from __future__ import annotations

import json
import os
import time
import tempfile
from typing import Any, Dict, List
from app.analysis.measurement_quality import measurement_quality

HISTORY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "data", "history")


def _ensure_dir() -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)


def _history_files() -> List[str]:
    _ensure_dir()
    files = [f for f in os.listdir(HISTORY_DIR) if f.endswith(".json")]
    files.sort()
    return [os.path.join(HISTORY_DIR, f) for f in files]


def _atomic_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(data, output, separators=(',', ':'))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _summary(data):
    return {**{key: data.get(key) for key in ('id', 'created_at', 'profiles_count',
        'distance_mm', 'analysis_duration_ms', 'metrics')},
        'points_count': len(data.get('original_points') or []),
        'augmented_points_count': len(data.get('augmented_points') or []),
        **measurement_quality(data)}


def save_measurement(payload: Dict[str, Any], keep_last: int = 10) -> Dict[str, Any]:
    _ensure_dir()
    ts = int(time.time() * 1000)
    meas_id = str(time.time_ns())
    record = {
        "id": meas_id,
        "created_at": ts,
        **payload,
        **measurement_quality(payload),
    }
    path = os.path.join(HISTORY_DIR, f"{meas_id}.json")
    _atomic_json(path, record)
    _atomic_json(os.path.join(HISTORY_DIR, '.summaries', f'{meas_id}.json'), _summary(record))

    # prune old
    files = _history_files()
    if len(files) > keep_last:
        for old in files[: len(files) - keep_last]:
            try:
                os.remove(old)
                summary = os.path.join(HISTORY_DIR, '.summaries', os.path.basename(old))
                if os.path.exists(summary):
                    os.remove(summary)
            except OSError:
                pass

    return record


def list_measurements() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in _history_files():
        try:
            summary_path = os.path.join(HISTORY_DIR, '.summaries', os.path.basename(path))
            if os.path.exists(summary_path):
                with open(summary_path, 'r', encoding='utf-8') as f:
                    items.append(json.load(f))
            else:
                # Legacy archives are not parsed on every list request. Full details
                # remain available on demand via get_measurement.
                name = os.path.splitext(os.path.basename(path))[0]
                items.append(dict(id=name, created_at=int(os.path.getmtime(path)*1000),
                    summary_pending=True, metrics=None, **measurement_quality({})))
        except Exception:
            continue
    # newest first
    items.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return items


def get_measurement(meas_id: str) -> Dict[str, Any] | None:
    if os.path.basename(meas_id) != meas_id or '/' in meas_id or '\\' in meas_id:
        return None
    path = os.path.join(HISTORY_DIR, f"{meas_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            try:
                _atomic_json(os.path.join(HISTORY_DIR, '.summaries', f'{meas_id}.json'), _summary(data))
            except OSError:
                pass  # A read-only archive is still readable without a cached summary.
            return {**data, **measurement_quality(data)}
    except Exception:
        return None
