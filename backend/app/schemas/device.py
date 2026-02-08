from pydantic import BaseModel
from typing import List, Optional

class CalibrationData(BaseModel):
    translation: List[float]
    rotation_deg: List[float]
    scale: float = 1.0

class DeviceCreate(BaseModel):
    device_id: str
    name: str
    ip_address: str
    port: int = 2111
    enabled: bool = True
    segments_per_scan: int | None = None
    calibration: CalibrationData
    frame_corner: Optional[str] = None
    frame_position: Optional[List[float]] = None
    frame_rotation_deg: Optional[List[float]] = None
    acquisition_mode: str = "continuous"
    encoder_enabled: bool = False
    speed_profile: str = "fixed"

class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    segments_per_scan: Optional[int] = None
    calibration: Optional[CalibrationData] = None
    frame_corner: Optional[str] = None
    frame_position: Optional[List[float]] = None
    frame_rotation_deg: Optional[List[float]] = None
    acquisition_mode: Optional[str] = None
    encoder_enabled: Optional[bool] = None
    speed_profile: Optional[str] = None

class DeviceResponse(BaseModel):
    device_id: str
    name: str
    ip_address: str
    port: int
    enabled: bool
    connection_status: str
    calibration: CalibrationData
    segments_per_scan: int | None = None
    frame_corner: Optional[str] = None
    frame_position: Optional[List[float]] = None
    frame_rotation_deg: Optional[List[float]] = None
    acquisition_mode: str
    encoder_enabled: bool
    speed_profile: str

class AutoCalibrationRequest(BaseModel):
    device_ids: List[str]
    method: str = "icp"
    max_iterations: int = 50

class AutoCalibrationResult(BaseModel):
    device_id: str
    translation: List[float]
    rotation_deg: List[float]
    scale: float = 1.0
    score: Optional[float] = None

class AutoCalibrationResponse(BaseModel):
    method: str
    results: List[AutoCalibrationResult]

class ManualCalibrationRequest(BaseModel):
    device_id: str
    translation: List[float]
    rotation_deg: List[float]
    scale: float = 1.0

class FrameSettings(BaseModel):
    width_m: float
    height_m: float
    origin_mode: str

class MotionSettings(BaseModel):
    mode: str  # "fixed" or "encoder"
    fixed_speed_mps: float | None = None
    profiling_distance_mm: float | None = None
