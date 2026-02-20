"""
Picoscan Receiver - Nasłuchiwanie danych UDP od Picoscanu
PC jest SERWEREM, Picoscan wysyła dane UDP
"""
import logging
from typing import Optional, List, Tuple
import numpy as np
import threading
import time
from app.services.lms4000_receiver import Lms4000Receiver
from app.core.device_manager import device_manager

# Importy z ScanSegmentAPI
try:
    from scansegmentapi.scansegmentapi import msgpack as MsgpackApi
    from scansegmentapi.scansegmentapi import compact as CompactApi
    from scansegmentapi.scansegmentapi.udp_handler import UDPHandler
except ImportError:
    from scansegmentapi import msgpack as MsgpackApi
    from scansegmentapi import compact as CompactApi
    from scansegmentapi.udp_handler import UDPHandler

logger = logging.getLogger(__name__)


class PicoscanReceiver:
    """Nasłuchuje na danych UDP od Picoscanu (PC = SERVER)"""
    
    def __init__(self, listen_ip: str = "0.0.0.0", listen_port: int = 2115, format_type: str = "compact"):
        """
        Initialize receiver
        
        Args:
            listen_ip: IP do nasłuchiwania (0.0.0.0 = wszystkie interfejsy)
            listen_port: Port UDP (2115)
        """
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        ft = (format_type or "compact").lower()
        self.format_type = ft if ft in ("compact", "msgpack") else "compact"
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
            
            if self.format_type == "msgpack":
                self.receiver = MsgpackApi.Receiver(self.transport_layer)
            else:
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
                        if isinstance(s, str):
                            if not s.strip():
                                return len(s)
                            lowered = s.lower()
                            if "received segment" in lowered or ("received" in lowered and "segment" in lowered):
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
            "format": self.format_type,
            "total_segments_received": self.total_segments_received,
            "total_frames_received": self.total_frames_received,
            "last_frame_number": self.last_frame_number,
            "last_segment_counter": self.last_segment_counter,
            "last_receive_ts": self.last_receive_ts
        }
    
    def segments_to_point_cloud(self, segments: List[dict]) -> np.ndarray:
        """Convert segments to point cloud (X, Y, Z, RSSI)."""
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
            logger.error(f"Error converting: {e}")
            return np.array([], dtype=np.float32).reshape(0, 3)
    
    def _extract_points_compact(self, segment: dict) -> List[list]:
        """Extract points from Compact format segment"""
        points = []
        
        try:
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

    def _extract_points_msgpack(self, segment: dict) -> List[list]:
        """Extract points from msgpack format segment."""
        points = []

        try:
            for scan in segment.get("SegmentData", []):
                distances = scan.get("Distance", [])
                if not distances:
                    continue
                num_echos = len(distances)
                num_beams = len(distances[0]) if num_echos > 0 else 0
                if num_beams == 0:
                    continue

                phi = scan.get("Phi", 0.0)
                theta_start = scan.get("ThetaStart", 0.0)
                theta_stop = scan.get("ThetaStop", theta_start)
                channel_theta = scan.get("ChannelTheta")
                rssi = scan.get("Rssi")

                for beam_idx in range(num_beams):
                    if channel_theta is not None and len(channel_theta) > beam_idx:
                        theta = channel_theta[beam_idx]
                    else:
                        denom = (num_beams - 1) if num_beams > 1 else 1
                        theta = theta_start + beam_idx * (theta_stop - theta_start) / denom

                    for echo_idx in range(num_echos):
                        distance = distances[echo_idx][beam_idx]
                        if distance <= 0:
                            continue
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
            logger.warning(f"Error extracting msgpack points: {e}")

        return points
    
    def get_info(self) -> dict:
        """Get receiver info"""
        return {
            "listen_ip": self.listen_ip,
            "listen_port": self.listen_port,
            "listening": self.connected,
            "format": self.format_type
        }


class PicoscanReceiverManager:
    """Manage multiple receivers"""
    
    def __init__(self):
        self.receivers: dict = {}
        self._worker_threads: dict[str, threading.Thread] = {}
        self._worker_stops: dict[str, threading.Event] = {}

    def add_receiver(self, device_id: str, receiver: PicoscanReceiver) -> bool:
        """Add receiver"""
        self.receivers[device_id] = receiver
        return True
    
    def start_listening(
        self,
        device_id: str,
        listen_ip: str = "0.0.0.0",
        listen_port: int = 2115,
        segments_per_scan: int = None,
        format_type: str = "compact",
        device_type: str = "picoscan",
        sensor_ip: str | None = None,
    ) -> bool:
        """Start listening for a specific device"""
        try:
            # If receiver already exists and is listening, skip starting another
            if device_id in self.receivers:
                existing = self.receivers[device_id]
                if isinstance(existing, dict) and existing.get("listening"):
                    return True
                if isinstance(existing, PicoscanReceiver) and existing.connected:
                    return True

            dev_type = (device_type or "picoscan").lower()
            if dev_type == "lms4000":
                if not sensor_ip:
                    logger.error("Missing sensor_ip for LMS4000 receiver %s", device_id)
                    return False
                receiver = Lms4000Receiver(sensor_ip=sensor_ip, sensor_port=listen_port)
                ok = receiver.start_listening()
                if ok:
                    logger.info("Listening for LMS4000 on TCP %s:%s (%s)", sensor_ip, listen_port, device_id)
                    self.receivers[device_id] = {
                        "receiver": receiver,
                        "type": "lms4000",
                        "listening": True,
                        "listen_ip": sensor_ip,
                        "listen_port": listen_port,
                        "segments": [],
                        "segments_per_scan": 2,
                        "latest_points": None,
                        "latest_update_ts": None,
                        "frame_counter": 0,
                        "lock": threading.Lock(),
                    }
                    self._start_worker(device_id)
                return ok

            # One UDP socket bind per local (ip, port). Two picoscan receivers
            # on identical endpoint will conflict on Windows/Linux.
            for existing_id, existing_info in self.receivers.items():
                if existing_id == device_id:
                    continue
                if not isinstance(existing_info, dict):
                    continue
                if str(existing_info.get("type") or "picoscan").lower() != "picoscan":
                    continue
                same_ip = str(existing_info.get("listen_ip") or "") == str(listen_ip or "")
                same_port = int(existing_info.get("listen_port") or 0) == int(listen_port)
                if same_ip and same_port and bool(existing_info.get("listening")):
                    logger.error(
                        "Cannot start receiver %s on UDP %s:%s. Endpoint already used by %s. "
                        "Configure unique local UDP ports per Picoscan device.",
                        device_id,
                        listen_ip,
                        listen_port,
                        existing_id,
                    )
                    return False

            receiver = PicoscanReceiver(listen_ip, listen_port, format_type=format_type)
            if receiver.start_listening():
                # Store optional segments_per_scan provided from device config
                self.receivers[device_id] = {
                    "receiver": receiver,
                    "type": "picoscan",
                    "format_type": receiver.format_type,
                    "listening": True,
                    "listen_ip": listen_ip,
                    "listen_port": listen_port,
                    "segments": [],
                    "segments_per_scan": segments_per_scan,
                    "latest_points": None,
                    "latest_update_ts": None,
                    "frame_counter": 0,
                    "lock": threading.Lock(),
                }
                self._start_worker(device_id)
                return True
            return False
        except Exception as e:
            logger.error(f"Error starting listening for {device_id}: {e}")
            return False
    
    def stop_listening(self, device_id: str) -> bool:
        """Stop listening for a specific device"""
        try:
            stop_event = self._worker_stops.pop(device_id, None)
            if stop_event:
                stop_event.set()
            worker = self._worker_threads.pop(device_id, None)
            if worker and worker.is_alive():
                worker.join(timeout=1.0)
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

    def _start_worker(self, device_id: str):
        if device_id in self._worker_threads:
            w = self._worker_threads[device_id]
            if w.is_alive():
                return
        stop_event = threading.Event()
        self._worker_stops[device_id] = stop_event
        worker = threading.Thread(target=self._worker_loop, args=(device_id, stop_event), daemon=True)
        self._worker_threads[device_id] = worker
        worker.start()

    def _worker_loop(self, device_id: str, stop_event: threading.Event):
        while not stop_event.is_set():
            info = self.receivers.get(device_id)
            if not isinstance(info, dict):
                time.sleep(0.05)
                continue
            if not info.get("listening"):
                time.sleep(0.05)
                continue
            receiver = info.get("receiver")
            if not receiver or not getattr(receiver, "connected", False):
                time.sleep(0.05)
                continue
            rtype = str(info.get("type") or "picoscan").lower()
            try:
                points = None
                if rtype == "lms4000" and hasattr(receiver, "receive_point_cloud"):
                    scans_to_request = info.get("segments_per_scan") or 2
                    points = receiver.receive_point_cloud(int(scans_to_request))
                else:
                    segments_to_request = info.get("segments_per_scan")
                    if not segments_to_request:
                        segments_to_request = int((device_manager.point_cloud_settings or {}).get("segments_per_scan") or 1)
                    segments_to_request = max(1, int(segments_to_request))
                    segments = receiver.receive_segments(segments_to_request)
                    if segments:
                        segment_payload = segments[0] if isinstance(segments, tuple) else segments
                        segment_list = segment_payload if isinstance(segment_payload, list) else [segment_payload]
                        points = receiver.segments_to_point_cloud(segment_list)

                if points is not None and len(points) > 0:
                    lock = info.get("lock")
                    if lock is None:
                        info["latest_points"] = points
                        info["latest_update_ts"] = time.time()
                        info["frame_counter"] = int(info.get("frame_counter", 0) + 1)
                    else:
                        with lock:
                            info["latest_points"] = np.array(points, copy=True)
                            info["latest_update_ts"] = time.time()
                            info["frame_counter"] = int(info.get("frame_counter", 0) + 1)
                else:
                    time.sleep(0.01)
            except Exception as e:
                logger.debug("Receiver worker error [%s]: %s", device_id, e)
                time.sleep(0.05)

    def get_latest_point_cloud(self, device_id: str):
        info = self.receivers.get(device_id)
        if not isinstance(info, dict):
            return None
        lock = info.get("lock")
        pts = None
        frame = int(info.get("frame_counter", 0))
        ts = info.get("latest_update_ts")
        if lock is None:
            pts = info.get("latest_points")
        else:
            with lock:
                src = info.get("latest_points")
                if src is not None:
                    pts = np.array(src, copy=True)
        if pts is None:
            return None
        return {
            "points": pts,
            "frame": frame,
            "timestamp": ts,
        }
    
    def get_point_clouds(self, num_segments: int = 1) -> dict:
        """Get point clouds from all receivers"""
        point_clouds = {}
        for device_id, receiver_info in self.receivers.items():
            if isinstance(receiver_info, dict) and receiver_info.get("listening"):
                latest = self.get_latest_point_cloud(device_id)
                if latest and latest.get("points") is not None and len(latest["points"]) > 0:
                    point_clouds[device_id] = latest["points"]
        return point_clouds

    def get_point_clouds_for_devices(self, device_ids: List[str], num_segments: int = 1) -> dict:
        """Get point clouds only for requested device ids."""
        point_clouds = {}
        for device_id in (device_ids or []):
            receiver_info = self.receivers.get(device_id)
            if not (isinstance(receiver_info, dict) and receiver_info.get("listening")):
                continue
            latest = self.get_latest_point_cloud(device_id)
            if latest and latest.get("points") is not None and len(latest["points"]) > 0:
                point_clouds[device_id] = latest["points"]
        return point_clouds
