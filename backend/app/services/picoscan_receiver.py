"""
Picoscan Receiver - Nasłuchiwanie danych UDP od Picoscanu
PC jest SERWEREM, Picoscan wysyła dane UDP
"""
import logging
from typing import Optional, List, Tuple
import numpy as np

# Importy z ScanSegmentAPI
try:
    from scansegmentapi.scansegmentapi import compact as CompactApi
    from scansegmentapi.scansegmentapi.udp_handler import UDPHandler
except ImportError:
    from scansegmentapi import compact as CompactApi
    from scansegmentapi.udp_handler import UDPHandler

logger = logging.getLogger(__name__)


class PicoscanReceiver:
    """Nasłuchuje na danych UDP od Picoscanu (PC = SERVER)"""
    
    def __init__(self, listen_ip: str = "0.0.0.0", listen_port: int = 2115):
        """
        Initialize receiver
        
        Args:
            listen_ip: IP do nasłuchiwania (0.0.0.0 = wszystkie interfejsy)
            listen_port: Port UDP (2115)
        """
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.receiver = None
        self.transport_layer = None
        self.connected = False
        # Metrics
        self.total_segments_received = 0
        self.total_frames_received = 0
        self.last_frame_number = None
        self.last_segment_counter = None
        self.last_receive_ts = None
    
    def start_listening(self) -> bool:
        """Rozpocznij nasłuchiwanie UDP"""
        try:
            # UDP Handler nasłuchuje na porcie
            self.transport_layer = UDPHandler(
                self.listen_ip,
                self.listen_port,
                65535  # Max packet size
            )
            
            # Compact format receiver
            self.receiver = CompactApi.Receiver(self.transport_layer)
            
            self.connected = True
            logger.info(f"Listening for Picoscan on UDP {self.listen_ip}:{self.listen_port}")
            return True
        except Exception as e:
            logger.error(f"Failed to start listening: {e}")
            self.connected = False
            return False
    
    def stop_listening(self) -> bool:
        """Zatrzymaj nasłuchiwanie"""
        try:
            if self.receiver:
                self.receiver.close_connection()
            self.connected = False
            logger.info("Stopped listening")
            return True
        except Exception as e:
            logger.error(f"Error stopping: {e}")
            return False
    
    def receive_segments(self, num_segments: int = 1) -> Optional[Tuple]:
        """
        Receive scan segments from Picoscan
        
        Args:
            num_segments: Number of segments to receive
        
        Returns:
            Tuple of (segments, frameNumbers, segmentCounters) or None
        """
        if not self.connected:
            logger.error("Not listening")
            return None
        
        try:
            # Suppress noisy prints from third-party ScanSegmentAPI that write
            # directly to stdout (e.g. "Received segment X.") by temporarily
            # patching sys.stdout.write to filter those lines.
            from contextlib import contextmanager
            import sys

            @contextmanager
            def _suppress_scansegmentapi_prints():
                orig_write = sys.stdout.write

                def _write(s):
                    try:
                        if isinstance(s, str) and ("Received segment" in s or ("Received" in s and "segment" in s)):
                            return len(s)
                    except Exception:
                        pass
                    return orig_write(s)

                sys.stdout.write = _write
                try:
                    yield
                finally:
                    sys.stdout.write = orig_write

            with _suppress_scansegmentapi_prints():
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
                    # Use frame numbers from result tuple if present, else from segments
                    if isinstance(result, tuple) and len(result) > 1 and result[1]:
                        self.total_frames_received += len(result[1])
                        self.last_frame_number = result[1][-1]
                    else:
                        # Fallback: read from segment data
                        try:
                            last_seg = segments[-1]
                            if last_seg.get("Modules"):
                                self.last_frame_number = last_seg["Modules"][0].get("FrameNumber")
                                self.total_frames_received += 1
                        except Exception:
                            pass
                    # Segment counter
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
            logger.error(f"Error receiving: {e}")
            return None

    def get_metrics(self) -> dict:
        """Return receiver metrics"""
        return {
            "listen_ip": self.listen_ip,
            "listen_port": self.listen_port,
            "listening": self.connected,
            "total_segments_received": self.total_segments_received,
            "total_frames_received": self.total_frames_received,
            "last_frame_number": self.last_frame_number,
            "last_segment_counter": self.last_segment_counter,
            "last_receive_ts": self.last_receive_ts
        }
    
    def segments_to_point_cloud(self, segments: List[dict]) -> np.ndarray:
        """Convert Compact segments to point cloud (X, Y, Z, RSSI)"""
        points = []
        
        try:
            for segment in segments:
                points.extend(self._extract_points_compact(segment))
            
            if points:
                return np.array(points, dtype=np.float32)
            else:
                return np.array([], dtype=np.float32).reshape(0, 3)
        except Exception as e:
            logger.error(f"Error converting: {e}")
            return np.array([], dtype=np.float32).reshape(0, 3)
    
    def _extract_points_compact(self, segment: dict) -> List[list]:
        """Extract points from Compact format segment"""
        points = []
        
        try:
            # DEBUG: Log angle ranges from first segment to diagnose coordinate issues
            if not hasattr(self, '_angle_logged'):
                first_module = segment.get("Modules", [{}])[0]
                phi_list = first_module.get("Phi", [])
                theta_start_list = first_module.get("ThetaStart", [])
                theta_stop_list = first_module.get("ThetaStop", [])
                logger.warning(f"ANGLES DEBUG: Phi={phi_list}, ThetaStart={theta_start_list[:3]}, ThetaStop={theta_stop_list[:3]}")
                self._angle_logged = True
            
            for module_idx, module in enumerate(segment.get("Modules", [])):
                # Get angles for this module
                phi_list = module.get("Phi", [0.0])
                theta_start_list = module.get("ThetaStart", [0.0])
                theta_stop_list = module.get("ThetaStop", [0.0])
                
                for scan_idx, scan in enumerate(module.get("SegmentData", [])):
                    distances = scan.get("Distance", [])
                    if not distances:
                        continue

                    # distances is [echo][beam]
                    num_echos = len(distances)
                    num_beams = len(distances[0]) if num_echos > 0 else 0
                    if num_beams == 0:
                        continue
                    
                    # Get angle for this scan
                    phi = phi_list[scan_idx] if scan_idx < len(phi_list) else 0.0
                    theta_start = theta_start_list[scan_idx] if scan_idx < len(theta_start_list) else 0.0
                    theta_stop = theta_stop_list[scan_idx] if scan_idx < len(theta_stop_list) else theta_start
                    channel_theta = scan.get("ChannelTheta")
                    rssi = scan.get("Rssi")
                    
                    for beam_idx in range(num_beams):
                        # Prefer per-beam theta if available
                        if channel_theta is not None and len(channel_theta) > beam_idx:
                            theta = channel_theta[beam_idx]
                        else:
                            denom = (num_beams - 1) if num_beams > 1 else 1
                            theta = theta_start + beam_idx * (theta_stop - theta_start) / denom

                        for echo_idx in range(num_echos):
                            distance = distances[echo_idx][beam_idx]
                            if distance > 0:  # Skip invalid
                                # Angles are in radians per ScanSegmentAPI
                                x = distance * np.cos(theta) * np.cos(phi)
                                y = distance * np.cos(theta) * np.sin(phi)
                                z = distance * np.sin(theta)
                                rssi_val = 0.0
                                try:
                                    if rssi is not None and len(rssi) > echo_idx and len(rssi[echo_idx]) > beam_idx:
                                        rssi_val = float(rssi[echo_idx][beam_idx])
                                except Exception:
                                    rssi_val = 0.0
                                points.append([x, y, z, rssi_val])
        except Exception as e:
            logger.warning(f"Error extracting points: {e}")
        
        return points
    
    def get_info(self) -> dict:
        """Get receiver info"""
        return {
            "listen_ip": self.listen_ip,
            "listen_port": self.listen_port,
            "listening": self.connected,
            "format": "compact"
        }


class PicoscanReceiverManager:
    """Manage multiple receivers"""
    
    def __init__(self):
        self.receivers: dict = {}
    
    def add_receiver(self, device_id: str, receiver: PicoscanReceiver) -> bool:
        """Add receiver"""
        self.receivers[device_id] = receiver
        return True
    
    def start_listening(self, device_id: str, listen_ip: str = "0.0.0.0", listen_port: int = 2115, segments_per_scan: int = None) -> bool:
        """Start listening for a specific device"""
        try:
            # If receiver already exists and is listening, skip starting another
            if device_id in self.receivers:
                existing = self.receivers[device_id]
                if isinstance(existing, dict) and existing.get("listening"):
                    return True
                if isinstance(existing, PicoscanReceiver) and existing.connected:
                    return True

            receiver = PicoscanReceiver(listen_ip, listen_port)
            if receiver.start_listening():
                # Store optional segments_per_scan provided from device config
                self.receivers[device_id] = {"receiver": receiver, "listening": True, "listen_ip": listen_ip, "listen_port": listen_port, "segments": [], "segments_per_scan": segments_per_scan}
                return True
            else:
                return False
        except Exception as e:
            logger.error(f"Error starting listening for {device_id}: {e}")
            return False
    
    def stop_listening(self, device_id: str) -> bool:
        """Stop listening for a specific device"""
        try:
            if device_id in self.receivers:
                receiver_info = self.receivers[device_id]
                if isinstance(receiver_info, dict) and "receiver" in receiver_info:
                    receiver_info["receiver"].stop_listening()
                elif isinstance(receiver_info, PicoscanReceiver):
                    receiver_info.stop_listening()
                del self.receivers[device_id]
                return True
            return False
        except Exception as e:
            logger.error(f"Error stopping listening for {device_id}: {e}")
            return False
    
    def start_all(self) -> dict:
        """Start all receivers"""
        results = {}
        for device_id, receiver in self.receivers.items():
            if isinstance(receiver, dict):
                results[device_id] = receiver.get("listening", False)
            else:
                results[device_id] = receiver.start_listening()
        return results
    
    def stop_all(self) -> dict:
        """Stop all receivers"""
        results = {}
        for device_id in list(self.receivers.keys()):
            results[device_id] = self.stop_listening(device_id)
        return results
    
    def get_point_clouds(self, num_segments: int = 1) -> dict:
        """Get point clouds from all receivers"""
        point_clouds = {}
        for device_id, receiver_info in self.receivers.items():
            if isinstance(receiver_info, dict) and receiver_info.get("listening"):
                receiver = receiver_info.get("receiver")
                if receiver and receiver.connected:
                    # Determine segments to request: prefer per-receiver setting, otherwise use provided num_segments
                    segments_to_request = receiver_info.get("segments_per_scan") or num_segments or 10
                    try:
                        segments = receiver.receive_segments(segments_to_request)
                    except Exception:
                        segments = receiver.receive_segments(num_segments)
                    if segments:
                        # Normalize segments tuple to list
                        segment_payload = segments[0] if isinstance(segments, tuple) else segments
                        segment_list = segment_payload if isinstance(segment_payload, list) else [segment_payload]
                        point_clouds[device_id] = receiver.segments_to_point_cloud(segment_list)
        return point_clouds
