"""
Point Cloud Processor - handles point cloud operations
- Transformation (translation, rotation)
- Multi-device point cloud merging
- Missing data interpolation
- Measurement and dimensioning
"""
import numpy as np
from typing import List, Tuple, Optional
import logging
from scipy.interpolate import griddata
from scipy.spatial import cKDTree

logger = logging.getLogger(__name__)


class PointCloud:
    """Represents a 3D point cloud"""
    
    def __init__(self, points: np.ndarray, intensities: Optional[np.ndarray] = None):
        """
        Initialize point cloud
        
        Args:
            points: Nx3 array of (x, y, z) coordinates
            intensities: Optional N array of intensity values
        """
        self.points = np.array(points, dtype=np.float32)
        self.intensities = np.array(intensities, dtype=np.float32) if intensities is not None else None
        
        if self.points.shape[0] == 0:
            raise ValueError("Point cloud must contain at least one point")
        if self.points.shape[1] != 3:
            raise ValueError("Points must be Nx3 array")
    
    def __len__(self):
        return len(self.points)
    
    def get_bounds(self) -> dict:
        """Get bounding box of point cloud"""
        min_coords = self.points.min(axis=0)
        max_coords = self.points.max(axis=0)
        return {
            "min": {"x": float(min_coords[0]), "y": float(min_coords[1]), "z": float(min_coords[2])},
            "max": {"x": float(max_coords[0]), "y": float(max_coords[1]), "z": float(max_coords[2])},
            "size": {
                "x": float(max_coords[0] - min_coords[0]),
                "y": float(max_coords[1] - min_coords[1]),
                "z": float(max_coords[2] - min_coords[2])
            }
        }


class TransformationService:
    """Handles point cloud transformations"""
    
    @staticmethod
    def translate(points: np.ndarray, translation: List[float]) -> np.ndarray:
        """Translate points"""
        translation = np.array(translation, dtype=np.float32)
        return points + translation
    
    @staticmethod
    def rotate(points: np.ndarray, angles_deg: List[float], order: str = "xyz") -> np.ndarray:
        """
        Rotate points using Euler angles
        
        Args:
            points: Nx3 array
            angles_deg: Rotation angles in degrees [x_rot, y_rot, z_rot]
            order: Rotation order (xyz, zyx, etc.)
        
        Returns:
            Rotated Nx3 array
        """
        angles_rad = np.radians(angles_deg)
        
        # Get rotation matrices
        Rx = np.array([
            [1, 0, 0],
            [0, np.cos(angles_rad[0]), -np.sin(angles_rad[0])],
            [0, np.sin(angles_rad[0]), np.cos(angles_rad[0])]
        ], dtype=np.float32)
        
        Ry = np.array([
            [np.cos(angles_rad[1]), 0, np.sin(angles_rad[1])],
            [0, 1, 0],
            [-np.sin(angles_rad[1]), 0, np.cos(angles_rad[1])]
        ], dtype=np.float32)
        
        Rz = np.array([
            [np.cos(angles_rad[2]), -np.sin(angles_rad[2]), 0],
            [np.sin(angles_rad[2]), np.cos(angles_rad[2]), 0],
            [0, 0, 1]
        ], dtype=np.float32)
        
        # Combine rotation matrices
        if order == "xyz":
            R = Rz @ Ry @ Rx
        elif order == "zyx":
            R = Rx @ Ry @ Rz
        else:
            R = Rz @ Ry @ Rx
        
        return (R @ points.T).T
    
    @staticmethod
    def scale(points: np.ndarray, scale: float) -> np.ndarray:
        """Scale points"""
        return points * scale
    
    @staticmethod
    def apply_calibration(points: np.ndarray, calibration: dict) -> np.ndarray:
        """Apply full calibration (translation + rotation + scale)"""
        # Apply scale
        if "scale" in calibration:
            points = TransformationService.scale(points, calibration["scale"])
        
        # Apply rotation
        if "rotation_deg" in calibration:
            points = TransformationService.rotate(points, calibration["rotation_deg"])
        
        # Apply translation
        if "translation" in calibration:
            points = TransformationService.translate(points, calibration["translation"])
        
        return points


class PointCloudProcessor:
    """Main processor for point cloud operations"""
    
    @staticmethod
    def merge_point_clouds(point_clouds: List[Tuple[np.ndarray, dict]]) -> PointCloud:
        """
        Merge multiple point clouds with calibration
        
        Args:
            point_clouds: List of tuples (points, calibration_dict)
        
        Returns:
            Merged PointCloud
        """
        merged_points = []
        merged_intensities = []
        
        for points, calibration in point_clouds:
            # Apply calibration
            transformed = TransformationService.apply_calibration(points[:, :3], calibration)
            merged_points.append(transformed)
            
            # Handle intensities if available
            if points.shape[1] > 3:
                intensities = points[:, 3].astype(np.float32)
                merged_intensities.append(intensities)
        
        merged = np.vstack(merged_points)
        intensities = np.concatenate(merged_intensities) if merged_intensities else None
        
        return PointCloud(merged, intensities)
    
    @staticmethod
    def interpolate_missing_data(point_cloud: PointCloud, method: str = "kriging", 
                                grid_spacing: float = 5.0) -> PointCloud:
        """
        Interpolate missing data in point cloud
        
        Args:
            point_cloud: Input point cloud
            method: Interpolation method (kriging, idw, nearest)
            grid_spacing: Grid spacing for interpolation
        
        Returns:
            Point cloud with interpolated data
        """
        logger.info(f"Interpolating missing data using {method} method")
        
        points = point_cloud.points
        bounds = point_cloud.get_bounds()
        
        # Create regular grid
        x = np.arange(bounds["min"]["x"], bounds["max"]["x"], grid_spacing)
        y = np.arange(bounds["min"]["y"], bounds["max"]["y"], grid_spacing)
        z = np.arange(bounds["min"]["z"], bounds["max"]["z"], grid_spacing)
        
        xi, yi, zi = np.meshgrid(x, y, z, indexing='ij')
        grid_points = np.column_stack([xi.ravel(), yi.ravel(), zi.ravel()])
        
        if method == "nearest":
            # Use nearest neighbor interpolation
            tree = cKDTree(points)
            distances, indices = tree.query(grid_points, k=1)
            interpolated = points[indices]
        else:
            # Default to linear interpolation
            interpolated = griddata(points, points, grid_points, method='nearest')
        
        return PointCloud(interpolated)
    
    @staticmethod
    def measure_distance(point_cloud: PointCloud, p1_idx: int, p2_idx: int) -> float:
        """Measure distance between two points"""
        p1 = point_cloud.points[p1_idx]
        p2 = point_cloud.points[p2_idx]
        return float(np.linalg.norm(p2 - p1))
    
    @staticmethod
    def calculate_statistics(point_cloud: PointCloud) -> dict:
        """Calculate point cloud statistics"""
        points = point_cloud.points
        
        # Oblicz statystyki
        centroid = {
            "x": float(np.nanmean(points[:, 0])) if len(points) > 0 else 0.0,
            "y": float(np.nanmean(points[:, 1])) if len(points) > 0 else 0.0,
            "z": float(np.nanmean(points[:, 2])) if len(points) > 0 else 0.0,
        }
        
        bounds = point_cloud.get_bounds()
        
        # Bezpieczne obliczenie gęstości
        volume = (bounds["size"]["x"] * bounds["size"]["y"] * bounds["size"]["z"])
        density = float(len(point_cloud) / volume) if volume > 0 else 0.0
        
        # Zamień inf/nan na None
        def safe_float(val):
            if np.isnan(val) or np.isinf(val):
                return None
            return float(val)
        
        return {
            "num_points": len(point_cloud),
            "centroid": centroid,
            "bounds": bounds,
            "density": safe_float(density)
        }
    
    @staticmethod
    def filter_by_distance(point_cloud: PointCloud, center: List[float], 
                          max_distance: float) -> PointCloud:
        """Filter points by distance from center"""
        center = np.array(center)
        distances = np.linalg.norm(point_cloud.points - center, axis=1)
        mask = distances <= max_distance
        return PointCloud(point_cloud.points[mask])

    @staticmethod
    def clip_points_to_frame(points: np.ndarray, frame_settings: dict) -> np.ndarray:
        """
        Clip points to configured frame rectangle.

        Frame is defined in X/Z plane:
        - width_m maps to X axis
        - height_m maps to Z axis
        """
        if points is None or len(points) == 0:
            return points
        if points.ndim != 2 or points.shape[1] < 3:
            return points

        width_m = float((frame_settings or {}).get("width_m", 0.0) or 0.0)
        height_m = float((frame_settings or {}).get("height_m", 0.0) or 0.0)
        origin_mode = str((frame_settings or {}).get("origin_mode", "center") or "center")
        if width_m <= 0 or height_m <= 0:
            return points

        width_mm = width_m * 1000.0
        height_mm = height_m * 1000.0

        if origin_mode == "center":
            min_x = -width_mm / 2.0
            max_x = width_mm / 2.0
            min_z = -height_mm / 2.0
            max_z = height_mm / 2.0
        else:
            min_x = 0.0
            max_x = width_mm
            min_z = 0.0
            max_z = height_mm

        x = points[:, 0]
        z = points[:, 2]
        mask = (x >= min_x) & (x <= max_x) & (z >= min_z) & (z <= max_z)
        return points[mask]

    @staticmethod
    def filter_edge_points(points: np.ndarray, k: int = 12, curvature_threshold: float = 0.08) -> np.ndarray:
        """
        Keep geometrically "edgy" points using local curvature estimate.
        Curvature is computed from eigenvalues of local covariance in k-NN neighborhood.
        """
        if points is None or len(points) == 0:
            return points
        if points.ndim != 2 or points.shape[1] < 3:
            return points
        if len(points) < max(32, k + 1):
            return points

        xyz = points[:, :3].astype(np.float32, copy=False)
        tree = cKDTree(xyz)
        _, idx = tree.query(xyz, k=k + 1)
        if idx.ndim != 2 or idx.shape[1] < 3:
            return points

        # Vectorized covariance/eigendecomposition for all neighborhoods at once.
        neigh = xyz[idx]  # (N, k+1, 3)
        centered = neigh - np.mean(neigh, axis=1, keepdims=True)
        denom = float(max(1, centered.shape[1] - 1))
        cov = np.einsum("nki,nkj->nij", centered, centered) / denom  # (N, 3, 3)
        eigvals = np.linalg.eigvalsh(cov)  # (N, 3), ascending
        eps = 1e-9
        curvature = np.maximum(eigvals[:, 0], 0.0) / (np.sum(eigvals, axis=1) + eps)

        mask = curvature >= float(curvature_threshold)
        # Avoid empty result for strict thresholds.
        if int(np.count_nonzero(mask)) < 50:
            q = np.quantile(curvature, 0.85)
            mask = curvature >= q
        filtered = points[mask]
        return filtered if len(filtered) > 0 else points

    @staticmethod
    def filter_statistical_noise(points: np.ndarray, k: int = 16, std_ratio: float = 1.2) -> np.ndarray:
        """
        Remove isolated noisy points with Statistical Outlier Removal.
        Point is kept if mean distance to k nearest neighbors is not an outlier.
        """
        if points is None or len(points) == 0:
            return points
        if points.ndim != 2 or points.shape[1] < 3:
            return points
        if len(points) < max(40, k + 2):
            return points

        k = int(max(3, min(64, k)))
        std_ratio = float(max(0.1, min(5.0, std_ratio)))
        xyz = points[:, :3].astype(np.float32, copy=False)

        tree = cKDTree(xyz)
        dists, _ = tree.query(xyz, k=k + 1)
        if dists.ndim != 2 or dists.shape[1] < 2:
            return points

        # Exclude self-distance in column 0.
        mean_knn_dist = np.mean(dists[:, 1:], axis=1)
        mu = float(np.mean(mean_knn_dist))
        sigma = float(np.std(mean_knn_dist))
        if not np.isfinite(mu) or not np.isfinite(sigma):
            return points

        threshold = mu + std_ratio * sigma
        keep = mean_knn_dist <= threshold
        filtered = points[keep]
        return filtered if len(filtered) > 0 else points

    @staticmethod
    def _largest_connected_component_cells_2d(cells_2d: np.ndarray) -> set[tuple[int, int]]:
        if cells_2d is None or cells_2d.size == 0:
            return set()
        cell_set = {(int(c[0]), int(c[1])) for c in cells_2d}
        visited: set[tuple[int, int]] = set()
        best: set[tuple[int, int]] = set()
        neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
        for seed in cell_set:
            if seed in visited:
                continue
            stack = [seed]
            comp: set[tuple[int, int]] = set()
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                comp.add(node)
                for dx, dz in neighbors:
                    nxt = (node[0] + dx, node[1] + dz)
                    if nxt in cell_set and nxt not in visited:
                        stack.append(nxt)
            if len(comp) > len(best):
                best = comp
        return best

    @staticmethod
    def filter_voxel_density(
        points: np.ndarray,
        cell_mm: float = 8.0,
        min_points_per_cell: int = 3,
        keep_largest_component: bool = False,
    ) -> np.ndarray:
        """
        Remove sparse points using voxel occupancy in XYZ.
        Optionally keep only the largest connected component in XZ occupancy.
        """
        if points is None or len(points) == 0:
            return points
        if points.ndim != 2 or points.shape[1] < 3:
            return points

        cell_mm = float(max(2.0, cell_mm))
        min_pts = int(max(1, min_points_per_cell))
        coords = np.floor(points[:, :3] / cell_mm).astype(np.int32)

        uniq, inv, counts = np.unique(coords, axis=0, return_inverse=True, return_counts=True)
        dense_cells_mask = counts >= min_pts
        keep_dense = dense_cells_mask[inv]
        filtered = points[keep_dense]
        if len(filtered) == 0:
            return points

        if not keep_largest_component:
            return filtered

        filtered_coords = np.floor(filtered[:, :3] / cell_mm).astype(np.int32)
        filtered_xz = filtered_coords[:, [0, 2]]
        dense_xz = np.unique(filtered_xz, axis=0)
        largest = PointCloudProcessor._largest_connected_component_cells_2d(dense_xz)
        if not largest:
            return filtered
        keep_lcc = np.array([(int(c[0]), int(c[1])) in largest for c in filtered_xz], dtype=bool)
        filtered_lcc = filtered[keep_lcc]
        return filtered_lcc if len(filtered_lcc) > 0 else filtered

    @staticmethod
    def filter_region_xz(
        points: np.ndarray,
        min_x_mm: float,
        max_x_mm: float,
        min_z_mm: float,
        max_z_mm: float,
    ) -> np.ndarray:
        """
        Keep points inside X/Z rectangular region (all units in mm).
        """
        if points is None or len(points) == 0:
            return points
        if points.ndim != 2 or points.shape[1] < 3:
            return points
        x0 = float(min(min_x_mm, max_x_mm))
        x1 = float(max(min_x_mm, max_x_mm))
        z0 = float(min(min_z_mm, max_z_mm))
        z1 = float(max(min_z_mm, max_z_mm))
        x = points[:, 0]
        z = points[:, 2]
        mask = (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1)
        filtered = points[mask]
        return filtered if len(filtered) > 0 else points

    @staticmethod
    def filter_orthogonal_directions_xz(points: np.ndarray, angle_tolerance_deg: float = 12.0, k: int = 10) -> np.ndarray:
        """
        Keep points whose local direction in XZ plane is close to horizontal (0 deg) or vertical (90 deg).
        Useful for removing diagonal ghost/shadow streaks while keeping box side walls.
        """
        if points is None or len(points) == 0:
            return points
        if points.ndim != 2 or points.shape[1] < 3:
            return points
        if len(points) < max(24, k + 1):
            return points

        tol = float(max(1.0, min(45.0, angle_tolerance_deg)))
        xz = points[:, [0, 2]].astype(np.float32, copy=False)
        tree = cKDTree(xz)
        _, idx = tree.query(xz, k=k + 1)
        if idx.ndim != 2 or idx.shape[1] < 3:
            return points

        neigh = xz[idx]  # (N, k+1, 2)
        centered = neigh - np.mean(neigh, axis=1, keepdims=True)
        denom = float(max(1, centered.shape[1] - 1))
        cov = np.einsum("nki,nkj->nij", centered, centered) / denom  # (N, 2, 2)
        eigvals, eigvecs = np.linalg.eigh(cov)
        # Major direction = eigenvector for largest eigenvalue (column index 1 for 2x2, ascending eigvals)
        major = eigvecs[:, :, 1]  # (N, 2)
        angles = np.abs(np.degrees(np.arctan2(major[:, 1], major[:, 0])))
        angles = np.where(angles > 90.0, 180.0 - angles, angles)
        dist_to_axes = np.minimum(angles, np.abs(90.0 - angles))
        keep = dist_to_axes <= tol

        filtered = points[keep]
        return filtered if len(filtered) > 0 else points
