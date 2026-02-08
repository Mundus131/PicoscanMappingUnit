from fastapi import APIRouter, HTTPException, UploadFile, File
from typing import List, Optional
from app.services.point_cloud_processor import PointCloudProcessor, PointCloud
from app.core.device_manager import device_manager
import logging
import numpy as np
import json

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/merge")
async def merge_point_clouds(device_ids: List[str]):
    """
    Merge point clouds from multiple devices with their calibrations
    
    Args:
        device_ids: List of device IDs to merge
    
    Returns:
        Merged point cloud statistics
    """
    try:
        point_clouds = []
        
        for device_id in device_ids:
            device = device_manager.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
            
            # TODO: Get actual point cloud data from device
            # For now, create dummy data
            dummy_points = np.random.rand(100, 3) * 100
            point_clouds.append((dummy_points, device.calibration))
        
        merged = PointCloudProcessor.merge_point_clouds(point_clouds)
        stats = PointCloudProcessor.calculate_statistics(merged)
        
        return {
            "message": f"Merged {len(device_ids)} point clouds",
            "total_points": len(merged),
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"Error merging point clouds: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/interpolate-missing-data")
async def interpolate_missing_data(
    device_ids: List[str],
    method: str = "nearest",
    grid_spacing: float = 5.0
):
    """
    Interpolate missing data in merged point cloud
    
    Args:
        device_ids: Device IDs to merge and process
        method: Interpolation method (nearest, linear)
        grid_spacing: Grid spacing for interpolation
    
    Returns:
        Processing results
    """
    try:
        # Merge point clouds
        point_clouds = []
        for device_id in device_ids:
            device = device_manager.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
            
            dummy_points = np.random.rand(100, 3) * 100
            point_clouds.append((dummy_points, device.calibration))
        
        merged = PointCloudProcessor.merge_point_clouds(point_clouds)
        
        # Interpolate
        interpolated = PointCloudProcessor.interpolate_missing_data(
            merged,
            method=method,
            grid_spacing=grid_spacing
        )
        
        stats = PointCloudProcessor.calculate_statistics(interpolated)
        
        return {
            "message": "Interpolation completed",
            "interpolation_method": method,
            "grid_spacing": grid_spacing,
            "points_after": len(interpolated),
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"Error interpolating data: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/statistics")
async def get_point_cloud_statistics(device_ids: List[str]):
    """Get statistics for merged point cloud"""
    try:
        point_clouds = []
        for device_id in device_ids:
            device = device_manager.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
            
            dummy_points = np.random.rand(100, 3) * 100
            point_clouds.append((dummy_points, device.calibration))
        
        merged = PointCloudProcessor.merge_point_clouds(point_clouds)
        stats = PointCloudProcessor.calculate_statistics(merged)
        
        return stats
    except Exception as e:
        logger.error(f"Error calculating statistics: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/filter")
async def filter_point_cloud(
    device_ids: List[str],
    center: List[float],
    max_distance: float
):
    """Filter point cloud by distance from center"""
    try:
        if len(center) != 3:
            raise HTTPException(status_code=400, detail="Center must be [x, y, z]")
        
        point_clouds = []
        for device_id in device_ids:
            device = device_manager.get_device(device_id)
            if not device:
                raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
            
            dummy_points = np.random.rand(100, 3) * 100
            point_clouds.append((dummy_points, device.calibration))
        
        merged = PointCloudProcessor.merge_point_clouds(point_clouds)
        filtered = PointCloudProcessor.filter_by_distance(merged, center, max_distance)
        
        stats = PointCloudProcessor.calculate_statistics(filtered)
        
        return {
            "message": "Filtering completed",
            "original_points": len(merged),
            "filtered_points": len(filtered),
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"Error filtering point cloud: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/measure/{device_id}")
async def measure_distance(device_id: str, p1_idx: int, p2_idx: int):
    """Measure distance between two points in device data"""
    device = device_manager.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    
    # TODO: Get actual point cloud from device
    dummy_points = np.random.rand(100, 3) * 100
    cloud = PointCloud(dummy_points)
    
    if p1_idx >= len(cloud) or p2_idx >= len(cloud):
        raise HTTPException(status_code=400, detail="Point index out of range")
    
    distance = PointCloudProcessor.measure_distance(cloud, p1_idx, p2_idx)
    
    return {
        "p1_index": p1_idx,
        "p2_index": p2_idx,
        "distance": distance
    }
