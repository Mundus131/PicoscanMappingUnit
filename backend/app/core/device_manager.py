"""
Device Manager - handles multiple Picoscan devices
"""
import json
import logging
from typing import Dict, List, Optional
from config.settings import settings

logger = logging.getLogger(__name__)


class PicoscanDevice:
    """Represents a single Picoscan device"""
    
    def __init__(self, device_config: dict):
        self.device_id = device_config.get("device_id")
        self.name = device_config.get("name")
        self.ip_address = device_config.get("ip_address")
        self.port = device_config.get("port", 2111)
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
        self.connection_status = "disconnected"
        
    def __repr__(self):
        return f"PicoscanDevice({self.device_id}, {self.connection_status})"
    
    def to_dict(self):
        return {
            "device_id": self.device_id,
            "name": self.name,
            "ip_address": self.ip_address,
            "port": self.port,
            "enabled": self.enabled,
            "connection_status": self.connection_status,
            "calibration": self.calibration,
            "frame_corner": self.frame_corner,
            "frame_position": self.frame_position,
            "frame_rotation_deg": self.frame_rotation_deg,
            "segments_per_scan": self.segments_per_scan,
            "acquisition_mode": self.acquisition_mode,
            "encoder_enabled": self.encoder_enabled,
            "speed_profile": self.speed_profile
        }


class DeviceManager:
    """Manages all connected Picoscan devices"""
    
    def __init__(self):
        self.devices: Dict[str, PicoscanDevice] = {}
        self.point_cloud_settings = {}
        self.frame_settings = {}
        self.motion_settings = {}
        self.tdc_settings = {}
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
            self.tdc_settings = config.get("tdc_settings", {})
            # Ensure sensible default for segments_per_scan
            if "segments_per_scan" not in self.point_cloud_settings:
                self.point_cloud_settings["segments_per_scan"] = 10
            if "width_m" not in self.frame_settings:
                self.frame_settings["width_m"] = 2.0
            if "height_m" not in self.frame_settings:
                self.frame_settings["height_m"] = 1.2
            if "origin_mode" not in self.frame_settings:
                self.frame_settings["origin_mode"] = "center"
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
                "tdc_settings": self.tdc_settings,
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
