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
