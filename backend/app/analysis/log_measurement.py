from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple, Dict, Any
import math
import numpy as np


@dataclass
class SliceResult:
    position_mm: float
    center_mm: Tuple[float, float]
    radius_mm: float
    diameter_mm: float
    area_mm2: float
    circumference_mm: float
    points_used: int


def fit_circle_kasa(points_2d: np.ndarray) -> Tuple[float, float, float] | None:
    """Fit circle to 2D points using algebraic least squares (Kasa)."""
    if points_2d.shape[0] < 3:
        return None
    x = points_2d[:, 0].astype(np.float64)
    y = points_2d[:, 1].astype(np.float64)
    A = np.column_stack([2 * x, 2 * y, np.ones_like(x)])
    b = x * x + y * y
    try:
        c, *_ = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return None
    cx = float(c[0])
    cy = float(c[1])
    r_sq = c[2] + cx * cx + cy * cy
    if not math.isfinite(r_sq) or r_sq <= 0:
        return None
    r = math.sqrt(r_sq)
    return cx, cy, r


def compute_log_metrics(
    points: Iterable[Iterable[float]],
    profiling_distance_mm: float,
    window_profiles: int = 10,
    min_points: int = 50,
    y_min: float | None = None,
    y_max: float | None = None,
) -> Dict[str, Any]:
    """
    Compute log metrics by fitting circles on X/Z plane over windows of profiles.

    Assumptions:
    - Points are in mm.
    - Motion axis is Y (distance).
    - Profile plane is X/Z.
    """
    pts = np.array(list(points), dtype=np.float32)
    if pts.ndim != 2 or pts.shape[1] < 3:
        raise ValueError("points must be Nx3 (or Nx4 with RSSI)")
    if profiling_distance_mm <= 0:
        raise ValueError("profiling_distance_mm must be > 0")

    profile_index = np.rint(pts[:, 1] / profiling_distance_mm).astype(int)
    unique_profiles = np.unique(profile_index)
    unique_profiles.sort()

    window_profiles = max(1, int(window_profiles))
    slices: List[SliceResult] = []
    for start in range(0, len(unique_profiles), window_profiles):
        window = unique_profiles[start:start + window_profiles]
        if len(window) < window_profiles:
            break
        mask = np.isin(profile_index, window)
        window_pts = pts[mask]
        if window_pts.shape[0] < min_points:
            continue
        xz = window_pts[:, [0, 2]]
        fit = fit_circle_kasa(xz)
        if fit is None:
            continue
        cx, cz, r = fit
        diameter = 2.0 * r
        area = math.pi * r * r
        circumference = 2.0 * math.pi * r
        pos_mm = float(np.mean(window) * profiling_distance_mm)
        slices.append(SliceResult(
            position_mm=pos_mm,
            center_mm=(cx, cz),
            radius_mm=r,
            diameter_mm=diameter,
            area_mm2=area,
            circumference_mm=circumference,
            points_used=int(window_pts.shape[0]),
        ))

    if not slices:
        raise ValueError("Insufficient data for circle fitting.")

    slices.sort(key=lambda s: s.position_mm)
    if y_min is not None or y_max is not None:
        y_min_val = float(y_min) if y_min is not None else slices[0].position_mm
        y_max_val = float(y_max) if y_max is not None else slices[-1].position_mm
        if y_min_val < slices[0].position_mm - 1e-6:
            s0 = slices[0]
            slices.insert(0, SliceResult(
                position_mm=y_min_val,
                center_mm=s0.center_mm,
                radius_mm=s0.radius_mm,
                diameter_mm=s0.diameter_mm,
                area_mm2=s0.area_mm2,
                circumference_mm=s0.circumference_mm,
                points_used=0,
            ))
        if y_max_val > slices[-1].position_mm + 1e-6:
            s1 = slices[-1]
            slices.append(SliceResult(
                position_mm=y_max_val,
                center_mm=s1.center_mm,
                radius_mm=s1.radius_mm,
                diameter_mm=s1.diameter_mm,
                area_mm2=s1.area_mm2,
                circumference_mm=s1.circumference_mm,
                points_used=0,
            ))
    diameters = [s.diameter_mm for s in slices]
    areas = [s.area_mm2 for s in slices]
    positions = [s.position_mm for s in slices]

    volume_mm3 = 0.0
    for i in range(len(slices) - 1):
        dx = positions[i + 1] - positions[i]
        volume_mm3 += (areas[i] + areas[i + 1]) * 0.5 * dx

    if y_min is not None or y_max is not None:
        total_length_mm = (float(y_max) if y_max is not None else positions[-1]) - (float(y_min) if y_min is not None else positions[0])
    else:
        total_length_mm = positions[-1] - positions[0] if len(positions) > 1 else 0.0

    return {
        "window_profiles": window_profiles,
        "profiling_distance_mm": profiling_distance_mm,
        "total_slices": len(slices),
        "total_length_mm": total_length_mm,
        "y_min_mm": float(y_min) if y_min is not None else positions[0],
        "y_max_mm": float(y_max) if y_max is not None else positions[-1],
        "diameter_mm": {
            "min": float(min(diameters)),
            "max": float(max(diameters)),
            "avg": float(sum(diameters) / len(diameters)),
        },
        "volume_mm3": volume_mm3,
        "volume_m3": volume_mm3 / 1e9,
        "slices": [
            {
                "position_mm": s.position_mm,
                "center_mm": [s.center_mm[0], s.center_mm[1]],
                "radius_mm": s.radius_mm,
                "diameter_mm": s.diameter_mm,
                "area_mm2": s.area_mm2,
                "circumference_mm": s.circumference_mm,
                "points_used": s.points_used,
            }
            for s in slices
        ],
    }


def build_augmented_cloud(
    metrics: Dict[str, Any],
    points_per_circle: int = 180,
    rssi_value: float | None = 80.0,
    profiling_distance_mm: float | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
) -> List[List[float]]:
    """
    Build a synthetic point cloud by completing circles for each fitted slice.

    Returns points as [x, y, z, rssi?] in mm with Y as motion axis.
    """
    slices = metrics.get("slices") or []
    if not slices:
        return []
    if profiling_distance_mm is None:
        profiling_distance_mm = float(metrics.get("profiling_distance_mm") or 10.0)
    profiling_distance_mm = max(1e-6, float(profiling_distance_mm))
    points: List[List[float]] = []
    step = 2 * math.pi / max(12, int(points_per_circle))
    slices_sorted = sorted(slices, key=lambda s: s["position_mm"])
    positions = [float(s["position_mm"]) for s in slices_sorted]

    def interp_at(y: float) -> Tuple[float, float, float]:
        if y <= positions[0]:
            s = slices_sorted[0]
            return float(s["center_mm"][0]), float(s["center_mm"][1]), float(s["radius_mm"])
        if y >= positions[-1]:
            s = slices_sorted[-1]
            return float(s["center_mm"][0]), float(s["center_mm"][1]), float(s["radius_mm"])
        for i in range(len(positions) - 1):
            y0 = positions[i]
            y1 = positions[i + 1]
            if y0 <= y <= y1:
                t = (y - y0) / (y1 - y0) if y1 != y0 else 0.0
                s0 = slices_sorted[i]
                s1 = slices_sorted[i + 1]
                cx = float(s0["center_mm"][0]) + t * (float(s1["center_mm"][0]) - float(s0["center_mm"][0]))
                cz = float(s0["center_mm"][1]) + t * (float(s1["center_mm"][1]) - float(s0["center_mm"][1]))
                r = float(s0["radius_mm"]) + t * (float(s1["radius_mm"]) - float(s0["radius_mm"]))
                return cx, cz, r
        s = slices_sorted[-1]
        return float(s["center_mm"][0]), float(s["center_mm"][1]), float(s["radius_mm"])

    y_start = y_min if y_min is not None else positions[0]
    y_end = y_max if y_max is not None else positions[-1]
    y = y_start
    while y <= y_end + 1e-6:
        cx, cz, r = interp_at(y)
        ang = 0.0
        while ang < 2 * math.pi - 1e-6:
            x = cx + r * math.cos(ang)
            z = cz + r * math.sin(ang)
            if rssi_value is None:
                points.append([x, y, z])
            else:
                points.append([x, y, z, float(rssi_value)])
            ang += step
        y += profiling_distance_mm
    return points
