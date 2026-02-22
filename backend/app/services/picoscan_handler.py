"""
Picoscan Handler -Real communication with SICK Picoscan LIDAR devices
"""
import logging
from typing import Tuple, Optional, List
import numpy as np
import io
import threading
from contextlib import redirect_stdout, redirect_stderr

# Importy z ScanSegmentAPI - prawidłowe ścieżki
try:
    from scansegmentapi.scansegmentapi import msgpack as MSGPACKApi
    from scansegmentapi.scansegmentapi import compact as CompactApi
    from scansegmentapi.scansegmentapi.udp_handler import UDPHandler
    from scansegmentapi.scansegmentapi.tcp_handler import TCPHandler
    from scansegmentapi.scansegmentapi.compact_stream_extractor import CompactStreamExtractor
except ImportError:
    # Fallback dla innej struktury pakietu
    from scansegmentapi import msgpack as MSGPACKApi
    from scansegmentapi import compact as CompactApi
    from scansegmentapi.udp_handler import UDPHandler
    from scansegmentapi.tcp_handler import TCPHandler
    from scansegmentapi.compact_stream_extractor import CompactStreamExtractor

logger = logging.getLogger(__name__)
_scansegmentapi_stdio_lock = threading.Lock()


class PicoscanHandler:
    """Handles real communication with Picoscan LIDAR devices"""
    
    def __init__(self, ip_address: str, port: int = 2115, protocol: str = "tcp", 
                 format_type: str = "compact", timeout: int = 5):
        """
        Initialize Picoscan handler
        
        Args:
            ip_address: IP address for UDP (client PC) or TCP (sensor)
            port: Port number (default 2115)
            protocol: Transport protocol - "udp" or "tcp"
            format_type: Data format - "msgpack" or "compact"
            timeout: Connection timeout in seconds
        """
        self.ip_address = ip_address
        self.port = port
        self.protocol = protocol.lower()
        self.format_type = format_type.lower()
        self.timeout = timeout
        self.receiver = None
        self.transport_layer = None
        self.connected = False
        # Metrics
        self.total_segments_received = 0
        self.total_frames_received = 0
        self.last_frame_number = None
        self.last_segment_counter = None
        self.last_receive_ts = None
        
        if self.format_type not in ["msgpack", "compact"]:
            raise ValueError("format_type must be 'msgpack' or 'compact'")
        if self.protocol not in ["udp", "tcp"]:
            raise ValueError("protocol must be 'udp' or 'tcp'")
    
    def connect(self) -> bool:
        """Connect to Picoscan device"""
        try:
            if self.protocol == "udp":
                self.transport_layer = UDPHandler(
                    self.ip_address,
                    self.port,
                    65535
                )
            else:  # TCP
                stream_extractor = CompactStreamExtractor() if self.format_type == "compact" else None
                self.transport_layer = TCPHandler(
                    stream_extractor,
                    self.ip_address,
                    self.port,
                    1024
                )
            
            # Initialize receiver based on format
            if self.format_type == "msgpack":
                self.receiver = MSGPACKApi.Receiver(self.transport_layer)
            else:  # compact
                self.receiver = CompactApi.Receiver(self.transport_layer)
            
            self.connected = True
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Picoscan: {e}")
            self.connected = False
            return False
    
    def disconnect(self) -> bool:
        """Disconnect from device"""
        try:
            if self.receiver:
                self.receiver.close_connection()
            self.connected = False
            return True
        except Exception as e:
            logger.error(f"Error disconnecting: {e}")
            return False
    
    def receive_segments(self, num_segments: int = 1) -> Optional[Tuple]:
        """
        Receive scan segments from device
        
        Args:
            num_segments: Number of segments to receive
        
        Returns:
            Tuple of (segments, frameNumbers, segmentCounters) or None on error
        """
        if not self.connected:
            return None
        
        try:
            # Silence noisy direct stdout prints from ScanSegmentAPI.
            with _scansegmentapi_stdio_lock:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = self.receiver.receive_segments(num_segments)

            # Treat empty results (timeout/no data) as no data
            if not result:
                return None
            if isinstance(result, tuple) and len(result) > 0 and not result[0]:
                return None

            # Update metrics
            try:
                segments = result[0] if isinstance(result, tuple) else result
                if segments:
                    self.total_segments_received += len(segments)
                    if isinstance(result, tuple) and len(result) > 1 and result[1]:
                        self.total_frames_received += len(result[1])
                        self.last_frame_number = result[1][-1]
                    else:
                        try:
                            last_seg = segments[-1]
                            if last_seg.get("Modules"):
                                self.last_frame_number = last_seg["Modules"][0].get("FrameNumber")
                                self.total_frames_received += 1
                        except Exception:
                            pass
                    try:
                        last_seg = segments[-1]
                        if last_seg.get("Modules"):
                            self.last_segment_counter = last_seg["Modules"][0].get("SegmentCounter")
                    except Exception:
                        pass
                    import time
                    self.last_receive_ts = time.time()
            except Exception:
                pass

            return result
        except Exception as e:
            logger.error(f"Error receiving segments: {e}")
            return None

    def get_metrics(self) -> dict:
        """Return receiver metrics"""
        return {
            "ip_address": self.ip_address,
            "port": self.port,
            "protocol": self.protocol,
            "format": self.format_type,
            "connected": self.connected,
            "total_segments_received": self.total_segments_received,
            "total_frames_received": self.total_frames_received,
            "last_frame_number": self.last_frame_number,
            "last_segment_counter": self.last_segment_counter,
            "last_receive_ts": self.last_receive_ts
        }
    
    def segments_to_point_cloud(self, segments: List[dict]) -> np.ndarray:
        """
        Convert segments to point cloud (X, Y, Z coordinates)
        
        Args:
            segments: List of segment dictionaries from receiver
        
        Returns:
            Nx3 array of (x, y, z) coordinates
        """
        points = []
        
        try:
            for segment in segments:
                if self.format_type == "msgpack":
                    points.extend(self._extract_points_msgpack(segment))
                else:
                    points.extend(self._extract_points_compact(segment))
            
            if points:
                return np.array(points, dtype=np.float32)
            else:
                return np.array([], dtype=np.float32).reshape(0, 3)
        except Exception as e:
            logger.error(f"Error converting segments to point cloud: {e}")
            return np.array([], dtype=np.float32).reshape(0, 3)
    
    def _extract_points_msgpack(self, segment: dict) -> List[list]:
        """Extract points from MSGPACK format segment"""
        points = []
        
        try:
            for scan_idx, scan in enumerate(segment.get("SegmentData", [])):
                distances = scan.get("Distance", [])
                if not distances:
                    continue
                # distances is [echo][beam]
                num_echos = len(distances)
                num_beams = len(distances[0]) if num_echos > 0 else 0
                if num_beams == 0:
                    continue

                phi = scan.get("Phi", 0.0)
                theta_start = scan.get("ThetaStart", 0.0)
                theta_stop = scan.get("ThetaStop", theta_start)
                channel_theta = scan.get("ChannelTheta")
                
                for beam_idx in range(num_beams):
                    if channel_theta is not None and len(channel_theta) > beam_idx:
                        theta = channel_theta[beam_idx]
                    else:
                        denom = (num_beams - 1) if num_beams > 1 else 1
                        theta = theta_start + beam_idx * (theta_stop - theta_start) / denom

                    for echo_idx in range(num_echos):
                        distance = distances[echo_idx][beam_idx]
                        if distance > 0:  # Skip invalid distances
                            # Angles are in radians per ScanSegmentAPI
                            x = distance * np.cos(theta) * np.cos(phi)
                            y = distance * np.cos(theta) * np.sin(phi)
                            z = distance * np.sin(theta)
                            points.append([x, y, z])
        except Exception:
            pass
        
        return points
    
    def _extract_points_compact(self, segment: dict) -> List[list]:
        """Extract points from Compact format segment"""
        points = []
        
        try:
            for module_idx, module in enumerate(segment.get("Modules", [])):
                for scan_idx, scan in enumerate(module.get("SegmentData", [])):
                    distances = scan.get("Distance", [])
                    if not distances:
                        continue
                    # distances is [echo][beam]
                    num_echos = len(distances)
                    num_beams = len(distances[0]) if num_echos > 0 else 0
                    if num_beams == 0:
                        continue

                    phi = module.get("Phi", [0.0])[scan_idx] if module.get("Phi") else 0.0
                    theta_start = module.get("ThetaStart", [0.0])[scan_idx] if module.get("ThetaStart") else 0.0
                    theta_stop = module.get("ThetaStop", [0.0])[scan_idx] if module.get("ThetaStop") else theta_start
                    channel_theta = scan.get("ChannelTheta")
                    
                    for beam_idx in range(num_beams):
                        if channel_theta is not None and len(channel_theta) > beam_idx:
                            theta = channel_theta[beam_idx]
                        else:
                            denom = (num_beams - 1) if num_beams > 1 else 1
                            theta = theta_start + beam_idx * (theta_stop - theta_start) / denom

                        for echo_idx in range(num_echos):
                            distance = distances[echo_idx][beam_idx]
                            if distance > 0:
                                x = distance * np.cos(theta) * np.cos(phi)
                                y = distance * np.cos(theta) * np.sin(phi)
                                z = distance * np.sin(theta)
                                points.append([x, y, z])
        except Exception:
            pass
        
        return points
    
    def get_device_info(self) -> dict:
        """Get device information"""
        return {
            "ip_address": self.ip_address,
            "port": self.port,
            "protocol": self.protocol,
            "format": self.format_type,
            "connected": self.connected
        }


class MultiPicoscanManager:
    """Manages multiple Picoscan devices"""
    
    def __init__(self):
        self.handlers: dict = {}
    
    def add_device(self, device_id: str, handler: PicoscanHandler) -> bool:
        """Add handler for device"""
        self.handlers[device_id] = handler
        logger.info(f"Added handler for device {device_id}")
        return True
    
    def connect_all(self) -> dict:
        """Connect all devices"""
        results = {}
        for device_id, handler in self.handlers.items():
            results[device_id] = handler.connect()
        return results
    
    def disconnect_all(self) -> dict:
        """Disconnect all devices"""
        results = {}
        for device_id, handler in self.handlers.items():
            results[device_id] = handler.disconnect()
        return results
    
    def get_point_clouds(self, num_segments: int = 1) -> dict:
        """Get point clouds from all connected devices"""
        point_clouds = {}
        for device_id, handler in self.handlers.items():
            if handler.connected:
                segments = handler.receive_segments(num_segments)
                if segments:
                    point_clouds[device_id] = handler.segments_to_point_cloud(segments[0] if isinstance(segments, tuple) else segments)
        return point_clouds
