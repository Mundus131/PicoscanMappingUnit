from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple
import numpy as np


def _fit_plane_z(points_xyz: np.ndarray) -> Tuple[float, float, float]:
    """Fit plane z = a*x + b*y + c by least squares."""
    A = np.column_stack([points_xyz[:, 0], points_xyz[:, 1], np.ones(points_xyz.shape[0], dtype=np.float64)])
    b = points_xyz[:, 2]
    coeffs, *_ = np.linalg.lstsq(A, b, rcond=None)
    return float(coeffs[0]), float(coeffs[1]), float(coeffs[2])


def _plane_signed_distance(points_xyz: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    # Plane form: -a*x - b*y + z - c = 0
    n = np.array([-a, -b, 1.0], dtype=np.float64)
    norm = np.linalg.norm(n)
    if norm <= 1e-12:
        norm = 1.0
    d = -c
    return (points_xyz @ n + d) / norm


def _plane_basis(normal: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    n = normal / max(1e-12, np.linalg.norm(normal))
    ref = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    if abs(np.dot(n, ref)) > 0.95:
        ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    u = np.cross(ref, n)
    u /= max(1e-12, np.linalg.norm(u))
    v = np.cross(n, u)
    v /= max(1e-12, np.linalg.norm(v))
    return u, v


def _largest_connected_component_cells(cells: np.ndarray) -> set[tuple[int, int]]:
    if cells.shape[0] == 0:
        return set()
    unique = {(int(c[0]), int(c[1])) for c in cells}
    visited: set[tuple[int, int]] = set()
    best_component: set[tuple[int, int]] = set()
    neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    for cell in unique:
        if cell in visited:
            continue
        stack = [cell]
        component: set[tuple[int, int]] = set()
        visited.add(cell)
        while stack:
            cx, cy = stack.pop()
            component.add((cx, cy))
            for dx, dy in neighbors:
                n = (cx + dx, cy + dy)
                if n in unique and n not in visited:
                    visited.add(n)
                    stack.append(n)
        if len(component) > len(best_component):
            best_component = component
    return best_component


def _fit_plane_general(points_xyz: np.ndarray) -> Tuple[np.ndarray, float]:
    """Fit plane n.x + d = 0 by SVD."""
    centroid = np.mean(points_xyz, axis=0)
    centered = points_xyz - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    n = vh[-1, :]
    norm = np.linalg.norm(n)
    if norm <= 1e-12:
        n = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    else:
        n = n / norm
    d = -float(np.dot(n, centroid))
    return n.astype(np.float64), d


def _fit_footprint_pca(uu: np.ndarray, vv: np.ndarray) -> Tuple[float, float, float]:
    """Returns (length_mm, width_mm, angle_deg) for 2D footprint points in conveyor basis."""
    uv = np.column_stack([uu, vv]).astype(np.float64)
    if uv.shape[0] < 3:
        return 0.0, 0.0, 0.0
    mean = np.mean(uv, axis=0)
    centered = uv - mean
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvecs = eigvecs[:, order]
    proj = centered @ eigvecs
    p0_min, p0_max = float(np.min(proj[:, 0])), float(np.max(proj[:, 0]))
    p1_min, p1_max = float(np.min(proj[:, 1])), float(np.max(proj[:, 1]))
    length_mm = max(0.0, p0_max - p0_min)
    width_mm = max(0.0, p1_max - p1_min)
    angle_rad = float(np.arctan2(eigvecs[1, 0], eigvecs[0, 0]))
    angle_deg = float(np.degrees(angle_rad))
    return length_mm, width_mm, angle_deg


def compute_conveyor_object_metrics(
    points: Iterable[Iterable[float]],
    plane_quantile: float = 0.35,
    plane_inlier_mm: float = 8.0,
    object_min_height_mm: float = 8.0,
    localization_algorithm: str = "object_cloud_bbox",
    top_plane_quantile: float = 0.88,
    top_plane_inlier_mm: float = 4.0,
    denoise_enabled: bool = True,
    denoise_cell_mm: float = 8.0,
    denoise_min_points_per_cell: int = 3,
    keep_largest_component: bool = True,
) -> Dict[str, Any]:
    """
    Fit conveyor plane and measure object above the plane.
    - Assumes points are in mm.
    - Treats points with signed distance > object_min_height_mm as object points.
    """
    pts = np.array(list(points), dtype=np.float32)
    if pts.ndim != 2 or pts.shape[1] < 3:
        raise ValueError("points must be Nx3 (or Nx4 with RSSI)")
    xyz = pts[:, :3].astype(np.float64)
    if xyz.shape[0] < 200:
        raise ValueError("Insufficient points for conveyor analysis")

    q = float(np.clip(plane_quantile, 0.05, 0.8))
    z_thr = float(np.quantile(xyz[:, 2], q))
    seed = xyz[xyz[:, 2] <= z_thr]
    if seed.shape[0] < 50:
        seed = xyz

    a, b, c = _fit_plane_z(seed)
    dist = _plane_signed_distance(xyz, a, b, c)
    inliers = np.abs(dist) <= float(max(1.0, plane_inlier_mm))
    if np.count_nonzero(inliers) >= 50:
        a, b, c = _fit_plane_z(xyz[inliers])
        dist = _plane_signed_distance(xyz, a, b, c)

    # Orient signed distance so object points are positive
    if float(np.quantile(dist, 0.90)) < 0:
        a, b, c = -a, -b, -c
        dist = -dist

    obj_mask = dist > float(max(1.0, object_min_height_mm))
    obj_pts = xyz[obj_mask]
    if obj_pts.shape[0] < 20:
        raise ValueError("Object points above conveyor not detected")

    n = np.array([-a, -b, 1.0], dtype=np.float64)
    n /= max(1e-12, np.linalg.norm(n))
    u, v = _plane_basis(n)

    plane_origin = np.array([0.0, 0.0, c], dtype=np.float64)
    obj_indices = np.where(obj_mask)[0]
    rel = obj_pts - plane_origin
    uu = rel @ u
    vv = rel @ v
    hh = _plane_signed_distance(obj_pts, a, b, c)

    if denoise_enabled:
        # Filter sparse "shadow/ghost" returns using 2D conveyor-plane occupancy.
        cell_mm = float(max(2.0, denoise_cell_mm))
        min_pts_cell = int(max(1, denoise_min_points_per_cell))
        cells = np.column_stack([
            np.floor(uu / cell_mm).astype(np.int32),
            np.floor(vv / cell_mm).astype(np.int32),
        ])
        uniq_cells, counts = np.unique(cells, axis=0, return_counts=True)
        dense_cells = uniq_cells[counts >= min_pts_cell]
        dense_set = {(int(c[0]), int(c[1])) for c in dense_cells}
        if keep_largest_component and dense_cells.shape[0] > 0:
            dense_set = _largest_connected_component_cells(dense_cells)
        if dense_set:
            keep_local = np.array(
                [(int(cells[i, 0]), int(cells[i, 1])) in dense_set for i in range(cells.shape[0])],
                dtype=bool,
            )
            if int(np.count_nonzero(keep_local)) >= 20:
                obj_pts = obj_pts[keep_local]
                uu = uu[keep_local]
                vv = vv[keep_local]
                hh = hh[keep_local]
                kept_obj_indices = obj_indices[keep_local]
                obj_mask = np.zeros_like(obj_mask, dtype=bool)
                obj_mask[kept_obj_indices] = True

    u_min, u_max = float(np.min(uu)), float(np.max(uu))
    v_min, v_max = float(np.min(vv)), float(np.max(vv))
    h_min, h_max = float(np.min(hh)), float(np.max(hh))
    localization_algorithm = localization_algorithm if localization_algorithm in {"object_cloud_bbox", "box_top_plane"} else "object_cloud_bbox"
    top_plane_info: Dict[str, Any] | None = None

    if localization_algorithm == "box_top_plane":
        tq = float(np.clip(top_plane_quantile, 0.6, 0.99))
        top_thr = float(np.quantile(hh, tq))
        top_seed_mask = hh >= top_thr
        if int(np.count_nonzero(top_seed_mask)) < 20:
            top_seed_mask = hh >= float(np.quantile(hh, 0.80))
        top_seed_pts = obj_pts[top_seed_mask]

        if top_seed_pts.shape[0] >= 20:
            tn, td = _fit_plane_general(top_seed_pts)
            # Use inliers around top plane to stabilize footprint.
            top_dist = np.abs(top_seed_pts @ tn + td)
            top_inliers_local = top_dist <= float(max(0.5, top_plane_inlier_mm))
            if int(np.count_nonzero(top_inliers_local)) >= 15:
                top_pts = top_seed_pts[top_inliers_local]
                top_hh = hh[top_seed_mask][top_inliers_local]
                top_uu = uu[top_seed_mask][top_inliers_local]
                top_vv = vv[top_seed_mask][top_inliers_local]
            else:
                top_pts = top_seed_pts
                top_hh = hh[top_seed_mask]
                top_uu = uu[top_seed_mask]
                top_vv = vv[top_seed_mask]

            length_mm, width_mm, footprint_angle_deg = _fit_footprint_pca(top_uu, top_vv)
            if length_mm <= 0 or width_mm <= 0:
                length_mm = max(0.0, u_max - u_min)
                width_mm = max(0.0, v_max - v_min)
            # For a box on conveyor, height is top plane distance over conveyor plane.
            height_mm = max(0.0, float(np.mean(top_hh)))
            h_max = max(h_max, height_mm)
            top_plane_info = {
                "points_count": int(top_pts.shape[0]),
                "height_avg_mm": float(np.mean(top_hh)),
                "height_min_mm": float(np.min(top_hh)),
                "height_max_mm": float(np.max(top_hh)),
                "footprint_angle_deg": footprint_angle_deg,
                "equation": {
                    "nx": float(tn[0]),
                    "ny": float(tn[1]),
                    "nz": float(tn[2]),
                    "d": float(td),
                },
            }
        else:
            length_mm = max(0.0, u_max - u_min)
            width_mm = max(0.0, v_max - v_min)
            height_mm = max(0.0, h_max - h_min)
    else:
        length_mm = max(0.0, u_max - u_min)
        width_mm = max(0.0, v_max - v_min)
        height_mm = max(0.0, h_max - h_min)
    bbox_volume_mm3 = length_mm * width_mm * height_mm

    centroid = np.mean(obj_pts, axis=0)
    plane_z = a * xyz[:, 0] + b * xyz[:, 1] + c
    rmse = float(np.sqrt(np.mean((xyz[inliers, 2] - plane_z[inliers]) ** 2))) if np.count_nonzero(inliers) else None

    return {
        "analysis_app": "conveyor_object",
        "plane": {
            "equation_z": {"a": a, "b": b, "c": c},
            "normal": [float(n[0]), float(n[1]), float(n[2])],
            "inliers_count": int(np.count_nonzero(inliers)),
            "rmse_mm": rmse,
        },
        "object": {
            "localization_algorithm": localization_algorithm,
            "points_count": int(obj_pts.shape[0]),
            "centroid_mm": [float(centroid[0]), float(centroid[1]), float(centroid[2])],
            "bbox_mm": {
                "length": length_mm,
                "width": width_mm,
                "height": height_mm,
            },
            "bbox_volume_mm3": bbox_volume_mm3,
            "bbox_volume_m3": bbox_volume_mm3 / 1e9,
            "height_above_plane_mm": {
                "min": h_min,
                "max": h_max,
                "avg": float(np.mean(hh)),
            },
            "top_plane": top_plane_info,
        },
        # Used by augmentation helper
        "_internal": {
            "a": a,
            "b": b,
            "c": c,
            "object_mask": obj_mask.tolist(),
        },
    }


def build_conveyor_augmented_cloud(
    points: Iterable[Iterable[float]],
    metrics: Dict[str, Any],
    max_points: int = 60000,
) -> List[List[float]]:
    pts = np.array(list(points), dtype=np.float32)
    if pts.ndim != 2 or pts.shape[1] < 3:
        return []
    xyz = pts[:, :3]

    internal = metrics.get("_internal") or {}
    mask = internal.get("object_mask")
    if isinstance(mask, list) and len(mask) == xyz.shape[0]:
        obj_pts = xyz[np.array(mask, dtype=bool)]
    else:
        obj_pts = xyz

    if obj_pts.shape[0] == 0:
        return []
    if obj_pts.shape[0] > max_points:
        idx = np.random.choice(obj_pts.shape[0], max_points, replace=False)
        obj_pts = obj_pts[idx]

    # Mark object points with high synthetic RSSI for easy coloring in viewer.
    rssi = np.full((obj_pts.shape[0], 1), 95.0, dtype=np.float32)
    out = np.concatenate([obj_pts.astype(np.float32), rssi], axis=1)
    return out.tolist()
