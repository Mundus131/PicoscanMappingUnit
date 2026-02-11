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
    encoder_wheel_mode: str | None = None  # "diameter" or "circumference"
    encoder_wheel_value_mm: float | None = None
    encoder_rps: float | None = None

class AnalysisSettings(BaseModel):
    active_app: str  # "log" | "conveyor_object"
    log_window_profiles: int | None = None
    log_min_points: int | None = None
    conveyor_plane_quantile: float | None = None
    conveyor_plane_inlier_mm: float | None = None
    conveyor_object_min_height_mm: float | None = None
    conveyor_object_max_points: int | None = None
    conveyor_denoise_enabled: bool | None = None
    conveyor_denoise_cell_mm: float | None = None
    conveyor_denoise_min_points_per_cell: int | None = None
    conveyor_keep_largest_component: bool | None = None

class TdcSettings(BaseModel):
    enabled: bool
    ip_address: str
    port: int
    login: str
    password: str
    realm: str | None = None
    trigger_input: str
    poll_interval_ms: int
    token_refresh_interval_s: float | None = None
    grpc_timeout_s: float | None = None
    encoder_port: str | None = None  # "1".."4"
    start_delay_mode: str | None = None  # "time" or "distance"
    start_delay_ms: float | None = None
    start_delay_mm: float | None = None
    stop_delay_mode: str | None = None  # "time" or "distance"
    stop_delay_ms: float | None = None
    stop_delay_mm: float | None = None
