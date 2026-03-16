"""
Device Manager - handles multiple Picoscan devices
"""
import json
import logging
from typing import Dict, List, Optional
from config.settings import settings

logger = logging.getLogger(__name__)


class PicoscanDevice:
    """Represents a single sensor device"""
    
    def __init__(self, device_config: dict):
        self.device_id = device_config.get("device_id")
        self.name = device_config.get("name")
        self.ip_address = device_config.get("ip_address")
        self.port = device_config.get("port", 2111)
        self.device_type = (device_config.get("device_type") or "picoscan").lower()
        self.protocol = str(device_config.get("protocol") or ("tcp" if self.device_type == "lms4000" else "udp")).lower()
        fmt = str(device_config.get("format_type") or ("lmdscandata" if self.device_type == "lms4000" else "compact")).lower()
        self._normalize_transport(fmt)
        self.enabled = device_config.get("enabled", True)
        # Number of segments that make up a full scan for this device
        # If None, system-wide/default value will be used
        self.segments_per_scan = device_config.get("segments_per_scan")
        self.calibration = device_config.get("calibration", {})
        self.frame_corner = device_config.get("frame_corner")
        self.frame_position = device_config.get("frame_position")
        self.frame_rotation_deg = device_config.get("frame_rotation_deg")
        self.acquisition_mode = device_config.get("acquisition_mode", "continuous")
        self.encoder_enabled = device_config.get("encoder_enabled", False)
        self.speed_profile = device_config.get("speed_profile", "fixed")
        # Optional fixed yaw correction for LMD stream calibration disambiguation.
        self.lmd_yaw_correction_deg = device_config.get("lmd_yaw_correction_deg")
        self.connection_status = "disconnected"

    def _normalize_transport(self, format_type_hint: str | None = None) -> None:
        """Normalize protocol/format to a valid transport combination."""
        fmt = str(format_type_hint or self.format_type or "compact").lower()
        if self.device_type == "lms4000":
            self.protocol = "tcp"
            self.format_type = "lmdscandata"
            return

        if fmt not in ("compact", "msgpack", "lmdscandata"):
            fmt = "compact"
        self.format_type = fmt
        # picoscan: compact/msgpack over UDP, lmdscandata over TCP
        self.protocol = "tcp" if self.format_type == "lmdscandata" else "udp"
        
    def __repr__(self):
        return f"PicoscanDevice({self.device_id}, {self.connection_status})"
    
    def to_dict(self):
        return {
            "device_id": self.device_id,
            "name": self.name,
            "ip_address": self.ip_address,
            "port": self.port,
            "device_type": self.device_type,
            "protocol": self.protocol,
            "format_type": self.format_type,
            "enabled": self.enabled,
            "connection_status": self.connection_status,
            "calibration": self.calibration,
            "frame_corner": self.frame_corner,
            "frame_position": self.frame_position,
            "frame_rotation_deg": self.frame_rotation_deg,
            "segments_per_scan": self.segments_per_scan,
            "acquisition_mode": self.acquisition_mode,
            "encoder_enabled": self.encoder_enabled,
            "speed_profile": self.speed_profile,
            "lmd_yaw_correction_deg": self.lmd_yaw_correction_deg,
        }


class DeviceManager:
    """Manages all connected Picoscan devices"""
    
    def __init__(self):
        self.devices: Dict[str, PicoscanDevice] = {}
        self.point_cloud_settings = {}
        self.frame_settings = {}
        self.motion_settings = {}
        self.analysis_settings = {}
        self.output_settings = {}
        self.tdc_settings = {}
        self.preview_filter_settings = {}
        self.load_configuration()
    
    def load_configuration(self):
        """Load devices configuration from JSON file"""
        try:
            config_path = settings.picoscan_devices_config
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            for device_config in config.get("devices", []):
                device = PicoscanDevice(device_config)
                self.devices[device.device_id] = device
                logger.info(f"Loaded device configuration: {device.device_id}")
            
            self.point_cloud_settings = config.get("point_cloud_settings", {})
            self.frame_settings = config.get("frame_settings", {})
            self.motion_settings = config.get("motion_settings", {})
            self.analysis_settings = config.get("analysis_settings", {})
            self.output_settings = config.get("output_settings", {})
            self.tdc_settings = config.get("tdc_settings", {})
            self.preview_filter_settings = config.get("preview_filter_settings", {})
            # Ensure sensible default for segments_per_scan
            if "segments_per_scan" not in self.point_cloud_settings:
                self.point_cloud_settings["segments_per_scan"] = 10
            if "receive_batch_segments" not in self.point_cloud_settings:
                self.point_cloud_settings["receive_batch_segments"] = 1
            if "require_complete_frames" not in self.point_cloud_settings:
                self.point_cloud_settings["require_complete_frames"] = True
            if "incomplete_frame_timeout_s" not in self.point_cloud_settings:
                self.point_cloud_settings["incomplete_frame_timeout_s"] = 0.35
            if "width_m" not in self.frame_settings:
                self.frame_settings["width_m"] = 2.0
            if "height_m" not in self.frame_settings:
                self.frame_settings["height_m"] = 1.2
            if "origin_mode" not in self.frame_settings:
                self.frame_settings["origin_mode"] = "center"
            if "clip_points_to_frame" not in self.frame_settings:
                self.frame_settings["clip_points_to_frame"] = False
            if "mode" not in self.motion_settings:
                self.motion_settings["mode"] = "fixed"
            if "fixed_speed_mps" not in self.motion_settings:
                self.motion_settings["fixed_speed_mps"] = 0.5
            if "profiling_distance_mm" not in self.motion_settings:
                self.motion_settings["profiling_distance_mm"] = 10.0
            if "encoder_wheel_mode" not in self.motion_settings:
                self.motion_settings["encoder_wheel_mode"] = "diameter"
            if "encoder_wheel_value_mm" not in self.motion_settings:
                self.motion_settings["encoder_wheel_value_mm"] = 100.0
            if "encoder_rps" not in self.motion_settings:
                self.motion_settings["encoder_rps"] = 0.0
            # Analysis defaults
            if "active_app" not in self.analysis_settings:
                self.analysis_settings["active_app"] = "log"
            active_app = str(self.analysis_settings.get("active_app", "log") or "log").strip().lower()
            if active_app not in {"log", "none"}:
                active_app = "log"
            self.analysis_settings["active_app"] = active_app
            if "log_window_profiles" not in self.analysis_settings:
                self.analysis_settings["log_window_profiles"] = 10
            if "log_min_points" not in self.analysis_settings:
                self.analysis_settings["log_min_points"] = 50
            if "conveyor_localization_algorithm" not in self.analysis_settings:
                self.analysis_settings["conveyor_localization_algorithm"] = "object_cloud_bbox"
            if "conveyor_plane_quantile" not in self.analysis_settings:
                self.analysis_settings["conveyor_plane_quantile"] = 0.35
            if "conveyor_plane_inlier_mm" not in self.analysis_settings:
                self.analysis_settings["conveyor_plane_inlier_mm"] = 8.0
            if "conveyor_object_min_height_mm" not in self.analysis_settings:
                self.analysis_settings["conveyor_object_min_height_mm"] = 8.0
            if "conveyor_object_max_points" not in self.analysis_settings:
                self.analysis_settings["conveyor_object_max_points"] = 60000
            if "conveyor_top_plane_quantile" not in self.analysis_settings:
                self.analysis_settings["conveyor_top_plane_quantile"] = 0.88
            if "conveyor_top_plane_inlier_mm" not in self.analysis_settings:
                self.analysis_settings["conveyor_top_plane_inlier_mm"] = 4.0
            if "conveyor_denoise_enabled" not in self.analysis_settings:
                self.analysis_settings["conveyor_denoise_enabled"] = True
            if "conveyor_denoise_cell_mm" not in self.analysis_settings:
                self.analysis_settings["conveyor_denoise_cell_mm"] = 8.0
            if "conveyor_denoise_min_points_per_cell" not in self.analysis_settings:
                self.analysis_settings["conveyor_denoise_min_points_per_cell"] = 3
            if "conveyor_keep_largest_component" not in self.analysis_settings:
                self.analysis_settings["conveyor_keep_largest_component"] = True
            # Output defaults
            if "enabled" not in self.output_settings:
                self.output_settings["enabled"] = False
            if "connection_mode" not in self.output_settings:
                self.output_settings["connection_mode"] = "server"
            # Output transport is fixed: TCP server mode.
            self.output_settings["connection_mode"] = "server"
            if "host" not in self.output_settings:
                self.output_settings["host"] = "0.0.0.0"
            if "port" not in self.output_settings:
                self.output_settings["port"] = 2120
            # Output transport is fixed: TCP port 2120.
            self.output_settings["port"] = 2120
            if "payload_mode" not in self.output_settings:
                self.output_settings["payload_mode"] = "ascii"
            if "separator" not in self.output_settings:
                self.output_settings["separator"] = ";"
            if "prefix" not in self.output_settings:
                self.output_settings["prefix"] = "\x02"
            if "suffix" not in self.output_settings:
                self.output_settings["suffix"] = "\x03"
            if "include_labels" not in self.output_settings:
                self.output_settings["include_labels"] = False
            if "float_precision" not in self.output_settings:
                self.output_settings["float_precision"] = 2
            if "length_unit" not in self.output_settings:
                self.output_settings["length_unit"] = "mm"
            if "volume_unit" not in self.output_settings:
                self.output_settings["volume_unit"] = "m3"
            if "selected_fields" not in self.output_settings:
                self.output_settings["selected_fields"] = [
                    "timestamp_iso",
                    "analysis_app",
                    "volume",
                    "length",
                    "diameter_start",
                    "diameter_end",
                    "diameter_avg",
                ]
            if "output_frame_items" not in self.output_settings:
                self.output_settings["output_frame_items"] = [
                    {"type": "field", "key": k, "label": ""}
                    for k in self.output_settings.get("selected_fields", [])
                ]
            # TDC defaults
            if "enabled" not in self.tdc_settings:
                self.tdc_settings["enabled"] = False
            if "ip_address" not in self.tdc_settings:
                self.tdc_settings["ip_address"] = "192.168.0.100"
            if "port" not in self.tdc_settings:
                self.tdc_settings["port"] = 8081
            if "login" not in self.tdc_settings:
                self.tdc_settings["login"] = "admin"
            if "password" not in self.tdc_settings:
                self.tdc_settings["password"] = "Welcome1!"
            if "realm" not in self.tdc_settings:
                self.tdc_settings["realm"] = "admin"
            if "trigger_input" not in self.tdc_settings:
                self.tdc_settings["trigger_input"] = "DI1"
            if "poll_interval_ms" not in self.tdc_settings:
                self.tdc_settings["poll_interval_ms"] = 200
            if "token_refresh_interval_s" not in self.tdc_settings:
                self.tdc_settings["token_refresh_interval_s"] = 300
            if "grpc_timeout_s" not in self.tdc_settings:
                self.tdc_settings["grpc_timeout_s"] = 5.0
            if "encoder_port" not in self.tdc_settings:
                self.tdc_settings["encoder_port"] = "1"
            if "start_delay_mode" not in self.tdc_settings:
                self.tdc_settings["start_delay_mode"] = "time"
            if "start_delay_ms" not in self.tdc_settings:
                self.tdc_settings["start_delay_ms"] = 0.0
            if "start_delay_mm" not in self.tdc_settings:
                self.tdc_settings["start_delay_mm"] = 0.0
            if "stop_delay_mode" not in self.tdc_settings:
                self.tdc_settings["stop_delay_mode"] = "time"
            if "stop_delay_ms" not in self.tdc_settings:
                self.tdc_settings["stop_delay_ms"] = 0.0
            if "stop_delay_mm" not in self.tdc_settings:
                self.tdc_settings["stop_delay_mm"] = 0.0
            # Preview filter defaults
            if "use_edge_filter" not in self.preview_filter_settings:
                self.preview_filter_settings["use_edge_filter"] = False
            if "edge_curvature_threshold" not in self.preview_filter_settings:
                self.preview_filter_settings["edge_curvature_threshold"] = 0.08
            if "use_voxel_denoise" not in self.preview_filter_settings:
                self.preview_filter_settings["use_voxel_denoise"] = False
            if "voxel_cell_mm" not in self.preview_filter_settings:
                self.preview_filter_settings["voxel_cell_mm"] = 8.0
            if "voxel_min_points_per_cell" not in self.preview_filter_settings:
                self.preview_filter_settings["voxel_min_points_per_cell"] = 3
            if "voxel_keep_largest_component" not in self.preview_filter_settings:
                self.preview_filter_settings["voxel_keep_largest_component"] = False
            if "use_region_filter" not in self.preview_filter_settings:
                self.preview_filter_settings["use_region_filter"] = False
            if "region_rect_norm" not in self.preview_filter_settings:
                self.preview_filter_settings["region_rect_norm"] = [0.2, 0.15, 0.8, 0.85]
            if "use_orthogonal_filter" not in self.preview_filter_settings:
                self.preview_filter_settings["use_orthogonal_filter"] = False
            if "orthogonal_angle_tolerance_deg" not in self.preview_filter_settings:
                self.preview_filter_settings["orthogonal_angle_tolerance_deg"] = 12.0
            if "use_noise_filter" not in self.preview_filter_settings:
                self.preview_filter_settings["use_noise_filter"] = False
            if "noise_filter_k" not in self.preview_filter_settings:
                self.preview_filter_settings["noise_filter_k"] = 16
            if "noise_filter_std_ratio" not in self.preview_filter_settings:
                self.preview_filter_settings["noise_filter_std_ratio"] = 1.2
            if "visible_device_ids" not in self.preview_filter_settings:
                self.preview_filter_settings["visible_device_ids"] = None
        except FileNotFoundError:
            logger.warning(f"Configuration file not found: {config_path}")
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
    
    def _save_to_json(self):
        """Save current device configuration to JSON file"""
        try:
            config_path = settings.picoscan_devices_config
            config = {
                "devices": [device.to_dict() for device in self.devices.values()],
                "point_cloud_settings": self.point_cloud_settings,
                "frame_settings": self.frame_settings,
                "motion_settings": self.motion_settings,
                "analysis_settings": self.analysis_settings,
                "output_settings": self.output_settings,
                "tdc_settings": self.tdc_settings,
                "preview_filter_settings": self.preview_filter_settings,
            }
            
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            logger.info(f"Device configuration saved to {config_path}")
        except Exception as e:
            logger.error(f"Error saving configuration to JSON: {e}")
            raise
    
    def get_all_devices(self) -> List[PicoscanDevice]:
        """Get all devices"""
        return list(self.devices.values())
    
    def get_device(self, device_id: str) -> Optional[PicoscanDevice]:
        """Get specific device"""
        return self.devices.get(device_id)
    
    def add_device(self, device_config: dict) -> PicoscanDevice:
        """Add new device"""
        device = PicoscanDevice(device_config)
        self.devices[device.device_id] = device
        self._save_to_json()
        logger.info(f"Added new device: {device.device_id}")
        return device
    
    def remove_device(self, device_id: str) -> bool:
        """Remove device"""
        if device_id in self.devices:
            del self.devices[device_id]
            self._save_to_json()
            logger.info(f"Removed device: {device_id}")
            return True
        return False
    
    def update_device(self, device_id: str, device_config: dict) -> bool:
        """Update device configuration"""
        device = self.get_device(device_id)
        if device:
            # Update all properties from config
            device.name = device_config.get("name", device.name)
            device.ip_address = device_config.get("ip_address", device.ip_address)
            device.port = device_config.get("port", device.port)
            if "device_type" in device_config and device_config.get("device_type"):
                device.device_type = str(device_config.get("device_type")).lower()
            if "protocol" in device_config and device_config.get("protocol"):
                device.protocol = str(device_config.get("protocol")).lower()
            if "format_type" in device_config and device_config.get("format_type"):
                device.format_type = str(device_config.get("format_type")).lower()
            device._normalize_transport(device.format_type)
            device.enabled = device_config.get("enabled", device.enabled)
            # Allow updating segments_per_scan per-device
            if "segments_per_scan" in device_config:
                device.segments_per_scan = device_config.get("segments_per_scan")
            device.calibration = device_config.get("calibration", device.calibration)
            if "frame_corner" in device_config:
                device.frame_corner = device_config.get("frame_corner")
            if "frame_position" in device_config:
                device.frame_position = device_config.get("frame_position")
            if "frame_rotation_deg" in device_config:
                device.frame_rotation_deg = device_config.get("frame_rotation_deg")
            device.acquisition_mode = device_config.get("acquisition_mode", device.acquisition_mode)
            device.encoder_enabled = device_config.get("encoder_enabled", device.encoder_enabled)
            device.speed_profile = device_config.get("speed_profile", device.speed_profile)
            if "lmd_yaw_correction_deg" in device_config:
                device.lmd_yaw_correction_deg = device_config.get("lmd_yaw_correction_deg")
            
            self._save_to_json()
            logger.info(f"Updated device: {device_id}")
            return True
        return False
    
    def update_device_calibration(self, device_id: str, calibration: dict) -> bool:
        """Update device calibration"""
        device = self.get_device(device_id)
        if device:
            device.calibration.update(calibration)
            self._save_to_json()
            logger.info(f"Updated calibration for {device_id}: {calibration}")
            return True
        return False
    
    def connect_device(self, device_id: str) -> bool:
        """Connect to device (placeholder)"""
        device = self.get_device(device_id)
        if device:
            # TODO: Implement actual device connection
            device.connection_status = "connected"
            logger.info(f"Connected to device: {device_id}")
            return True
        return False
    
    def disconnect_device(self, device_id: str) -> bool:
        """Disconnect from device (placeholder)"""
        device = self.get_device(device_id)
        if device:
            # TODO: Implement actual device disconnection
            device.connection_status = "disconnected"
            logger.info(f"Disconnected from device: {device_id}")
            return True
        return False


# Global device manager instance
device_manager = DeviceManager()
