from pydantic import BaseModel, field_validator
from typing import Any, Dict, List, Optional

class CalibrationData(BaseModel):
    translation: List[float]
    rotation_deg: List[float]
    scale: float = 1.0

class DeviceCreate(BaseModel):
    device_id: str
    name: str
    ip_address: str
    port: int = 2111
    device_type: str = "picoscan"
    protocol: str | None = None
    format_type: str | None = None
    enabled: bool = True
    segments_per_scan: int | None = None
    calibration: CalibrationData
    frame_corner: Optional[str] = None
    frame_position: Optional[List[float]] = None
    frame_rotation_deg: Optional[List[float]] = None
    acquisition_mode: str = "continuous"
    encoder_enabled: bool = False
    speed_profile: str = "fixed"
    lmd_yaw_correction_deg: float | None = None

    @field_validator("device_type")
    @classmethod
    def validate_device_type(cls, value: str) -> str:
        v = (value or "picoscan").lower()
        if v not in ("picoscan", "lms4000"):
            raise ValueError("device_type must be 'picoscan' or 'lms4000'")
        return v

    @field_validator("protocol")
    @classmethod
    def normalize_protocol(cls, value: str | None) -> str | None:
        return value.lower() if isinstance(value, str) else value

    @field_validator("format_type")
    @classmethod
    def validate_format_type(cls, value: str | None) -> str | None:
        if value is None:
            return value
        v = value.lower()
        if v not in ("compact", "msgpack", "lmdscandata"):
            raise ValueError("format_type must be 'compact', 'msgpack' or 'lmdscandata'")
        return v

class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    ip_address: Optional[str] = None
    port: Optional[int] = None
    enabled: Optional[bool] = None
    device_type: Optional[str] = None
    protocol: Optional[str] = None
    format_type: Optional[str] = None
    segments_per_scan: Optional[int] = None
    calibration: Optional[CalibrationData] = None
    frame_corner: Optional[str] = None
    frame_position: Optional[List[float]] = None
    frame_rotation_deg: Optional[List[float]] = None
    acquisition_mode: Optional[str] = None
    encoder_enabled: Optional[bool] = None
    speed_profile: Optional[str] = None
    lmd_yaw_correction_deg: Optional[float] = None

    @field_validator("device_type")
    @classmethod
    def validate_device_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        v = value.lower()
        if v not in ("picoscan", "lms4000"):
            raise ValueError("device_type must be 'picoscan' or 'lms4000'")
        return v

    @field_validator("protocol")
    @classmethod
    def normalize_protocol(cls, value: Optional[str]) -> Optional[str]:
        return value.lower() if isinstance(value, str) else value

    @field_validator("format_type")
    @classmethod
    def validate_format_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        v = value.lower()
        if v not in ("compact", "msgpack", "lmdscandata"):
            raise ValueError("format_type must be 'compact', 'msgpack' or 'lmdscandata'")
        return v

class DeviceResponse(BaseModel):
    device_id: str
    name: str
    ip_address: str
    port: int
    device_type: str = "picoscan"
    protocol: str | None = None
    format_type: str | None = None
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
    lmd_yaw_correction_deg: float | None = None

class AutoCalibrationRequest(BaseModel):
    device_ids: List[str]
    reference_device_id: Optional[str] = None
    method: str = "icp"
    max_iterations: int = 50
    save_result: bool = True

class AutoCalibrationResult(BaseModel):
    device_id: str
    translation: List[float]
    rotation_deg: List[float]
    scale: float = 1.0
    score: Optional[float] = None

class AutoCalibrationResponse(BaseModel):
    method: str
    saved: bool = True
    results: List[AutoCalibrationResult]


class AutoCalibrationApplyRequest(BaseModel):
    results: List[AutoCalibrationResult]


class CalibrationPreviewRequest(BaseModel):
    device_ids: List[str]
    max_points: int = 20000
    accumulate_frames: int = 1
    calibration_overrides: Optional[Dict[str, CalibrationData]] = None
    use_edge_filter: bool = False
    edge_curvature_threshold: float = 0.08
    use_voxel_denoise: bool = False
    voxel_cell_mm: float = 8.0
    voxel_min_points_per_cell: int = 3
    voxel_keep_largest_component: bool = False
    use_region_filter: bool = False
    region_min_x_mm: float | None = None
    region_max_x_mm: float | None = None
    region_min_z_mm: float | None = None
    region_max_z_mm: float | None = None
    use_orthogonal_filter: bool = False
    orthogonal_angle_tolerance_deg: float = 12.0
    use_noise_filter: bool = False
    noise_filter_k: int = 16
    noise_filter_std_ratio: float = 1.2


class PreviewFilterSettings(BaseModel):
    use_edge_filter: bool = False
    edge_curvature_threshold: float = 0.08
    use_voxel_denoise: bool = False
    voxel_cell_mm: float = 8.0
    voxel_min_points_per_cell: int = 3
    voxel_keep_largest_component: bool = False
    use_region_filter: bool = False
    region_rect_norm: List[float] = [0.2, 0.15, 0.8, 0.85]  # [x0, y0, x1, y1]
    use_orthogonal_filter: bool = False
    orthogonal_angle_tolerance_deg: float = 12.0
    use_noise_filter: bool = False
    noise_filter_k: int = 16
    noise_filter_std_ratio: float = 1.2
    visible_device_ids: Optional[List[str]] = None

class ManualCalibrationRequest(BaseModel):
    device_id: str
    translation: List[float]
    rotation_deg: List[float]
    scale: float = 1.0

class FrameSettings(BaseModel):
    width_m: float
    height_m: float
    origin_mode: str
    clip_points_to_frame: bool = False

class MotionSettings(BaseModel):
    mode: str  # "fixed" or "encoder"
    fixed_speed_mps: float | None = None
    profiling_distance_mm: float | None = None
    encoder_wheel_mode: str | None = None  # "diameter" or "circumference"
    encoder_wheel_value_mm: float | None = None
    encoder_rps: float | None = None

class AnalysisSettings(BaseModel):
    active_app: str  # "none" | "log"
    log_window_profiles: int | None = None
    log_min_points: int | None = None
    conveyor_localization_algorithm: str | None = None  # "object_cloud_bbox" | "box_top_plane"
    conveyor_plane_quantile: float | None = None
    conveyor_plane_inlier_mm: float | None = None
    conveyor_object_min_height_mm: float | None = None
    conveyor_object_max_points: int | None = None
    conveyor_top_plane_quantile: float | None = None
    conveyor_top_plane_inlier_mm: float | None = None
    conveyor_denoise_enabled: bool | None = None
    conveyor_denoise_cell_mm: float | None = None
    conveyor_denoise_min_points_per_cell: int | None = None
    conveyor_keep_largest_component: bool | None = None

class OutputSettings(BaseModel):
    enabled: bool = False
    connection_mode: str = "server"  # "server" | "client"
    host: str = "0.0.0.0"
    port: int = 2120
    payload_mode: str = "ascii"  # "ascii" | "json"
    separator: str = ";"
    prefix: str = "\u0002"
    suffix: str = "\u0003"
    include_labels: bool = False
    float_precision: int = 2
    length_unit: str = "mm"  # "mm" | "m"
    volume_unit: str = "m3"  # "m3" | "l" | "mm3"
    selected_fields: List[str] = [
        "timestamp_iso",
        "analysis_app",
        "volume",
        "length",
        "diameter_start",
        "diameter_end",
        "diameter_avg",
    ]
    output_frame_items: List[Dict[str, Any]] = []

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
