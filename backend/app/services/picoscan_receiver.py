"""
Picoscan Receiver - Nasłuchiwanie danych UDP od Picoscanu
PC jest SERWEREM, Picoscan wysyła dane UDP
"""
import logging
from typing import Optional, List, Tuple
import numpy as np
import threading
import time
from collections import deque
import io
from contextlib import redirect_stdout, redirect_stderr
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
_scansegmentapi_stdio_lock = threading.Lock()


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
        self.last_error: str | None = None
        # Metrics
        self.total_segments_received = 0
        self.total_frames_received = 0
        self.last_frame_number = None
        self.last_segment_counter = None
        self.last_receive_ts = None
        self._io_lock = threading.RLock()
    
    def start_listening(self) -> bool:
        """Rozpocznij nas??uchiwanie UDP"""
        with self._io_lock:
            try:
                # UDP Handler nas??uchuje na porcie
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
                self.last_error = None
                logger.info(f"Listening for Picoscan on UDP {self.listen_ip}:{self.listen_port}")
                return True
            except Exception as e:
                self.last_error = str(e)
                self.connected = False
                err = str(e)
                if "Address already in use" in err or "10048" in err:
                    logger.warning(f"Failed to start listening (endpoint busy): {e}")
                else:
                    logger.error(f"Failed to start listening: {e}")
                return False

    def stop_listening(self) -> bool:
        """Zatrzymaj nas??uchiwanie"""
        with self._io_lock:
            try:
                if self.receiver:
                    self.receiver.close_connection()
                self.receiver = None
                self.transport_layer = None
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

        with self._io_lock:
            try:
                if self.receiver is None or not hasattr(self.receiver, "transport_layer"):
                    self.connected = False
                    if not self.start_listening():
                        return None

                # ScanSegmentAPI prints "Received segment X." directly to stdout.
                # Silence only this call in a thread-safe way.
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
                if "transport_layer" in str(e):
                    self.connected = False
                    self.last_error = str(e)
                    logger.warning(
                        "Receiver transport state invalid on %s:%s, reconnect scheduled",
                        self.listen_ip,
                        self.listen_port,
                    )
                    return None
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
        self._auto_thread: threading.Thread | None = None
        self._auto_stop: threading.Event | None = None
        self._auto_retry_state: dict[str, dict] = {}
        self._lock = threading.Lock()

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
            # Cleanup stale listeners that no longer exist in config or are disabled.
            for existing_id, existing_info in list(self.receivers.items()):
                if existing_id == device_id:
                    continue
                cfg = device_manager.get_device(existing_id)
                if (cfg is None) or (not bool(getattr(cfg, "enabled", True))):
                    try:
                        self.stop_listening(existing_id)
                    except Exception:
                        pass

            # If receiver already exists and is listening, skip starting another
            if device_id in self.receivers:
                existing = self.receivers[device_id]
                if isinstance(existing, dict) and existing.get("listening"):
                    return True
                if isinstance(existing, PicoscanReceiver) and existing.connected:
                    return True

            dev_type = (device_type or "picoscan").lower()
            fmt = str(format_type or "compact").lower()
            use_lmd_stream = dev_type == "lms4000" or fmt == "lmdscandata"
            if use_lmd_stream:
                if not sensor_ip:
                    logger.error("Missing sensor_ip for LMDscandata receiver %s", device_id)
                    return False
                receiver = Lms4000Receiver(sensor_ip=sensor_ip, sensor_port=listen_port)
                ok = receiver.start_listening()
                if ok:
                    logger.info("Listening for LMDscandata on TCP %s:%s (%s)", sensor_ip, listen_port, device_id)
                    self.receivers[device_id] = {
                        "receiver": receiver,
                        "type": "lmdscandata",
                        "device_id": device_id,
                        "device_type": dev_type,
                        "format_type": "lmdscandata",
                        "listening": True,
                        "listen_ip": sensor_ip,
                        "listen_port": listen_port,
                        "segments": [],
                        "segments_per_scan": 2,
                        "latest_points": None,
                        "latest_update_ts": None,
                        "frame_counter": 0,
                        "lock": threading.Lock(),
                        "availability": "unknown",
                        "last_error": None,
                        "error_streak": 0,
                        "no_data_streak": 0,
                        "next_poll_ts": 0.0,
                        "poll_backoff_s": 0.0,
                        "data_timestamps": deque(maxlen=24),
                        "segment_observations": deque(maxlen=120),
                        "segment_estimate": None,
                        "segment_estimate_updated_ts": None,
                        "frame_segments_pending": {},
                        "frame_pending_ts": {},
                        "segment_timeout_observations": deque(maxlen=40),
                        "incomplete_frames_dropped": 0,
                        "last_seen_frame_number": None,
                        "last_poll_ts": None,
                        "active_stream": "lmdscandata",
                        "fallback_enabled": True,
                        "fallback_receiver": None,
                        "fallback_listen_ip": "0.0.0.0",
                        "fallback_listen_port": 2115 if int(listen_port) == 2111 else int(listen_port),
                        "fallback_formats": ["compact", "msgpack"],
                        "fallback_format_idx": 0,
                        "fallback_active_format": None,
                        "fallback_no_data_streak": 0,
                        "fallback_activations": 0,
                        "fallback_next_retry_ts": 0.0,
                        "fallback_block_reason": None,
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
                    "device_id": device_id,
                    "device_type": dev_type,
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
                    "availability": "unknown",
                    "last_error": None,
                    "error_streak": 0,
                    "no_data_streak": 0,
                    "next_poll_ts": 0.0,
                    "poll_backoff_s": 0.0,
                    "data_timestamps": deque(maxlen=24),
                    "segment_observations": deque(maxlen=120),
                    "segment_estimate": None,
                    "segment_estimate_updated_ts": None,
                    "frame_segments_pending": {},
                    "frame_pending_ts": {},
                    "segment_timeout_observations": deque(maxlen=40),
                    "incomplete_frames_dropped": 0,
                    "last_seen_frame_number": None,
                    "last_poll_ts": None,
                    "active_stream": f"scansegmentapi:{receiver.format_type}",
                    "fallback_enabled": False,
                    "fallback_receiver": None,
                    "fallback_listen_ip": None,
                    "fallback_listen_port": None,
                    "fallback_formats": [],
                    "fallback_format_idx": 0,
                    "fallback_active_format": None,
                    "fallback_no_data_streak": 0,
                    "fallback_activations": 0,
                    "fallback_next_retry_ts": 0.0,
                    "fallback_block_reason": None,
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
            receiver_info = self.receivers.pop(device_id, None)
            if receiver_info is None:
                return False
            if isinstance(receiver_info, dict) and "receiver" in receiver_info:
                self._stop_fallback_receiver(receiver_info)
                receiver_info["receiver"].stop_listening()
            elif isinstance(receiver_info, PicoscanReceiver):
                receiver_info.stop_listening()
            return True
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

    def _stop_fallback_receiver(self, info: dict):
        fb = info.get("fallback_receiver")
        if isinstance(fb, PicoscanReceiver):
            try:
                fb.stop_listening()
            except Exception:
                pass
        info["fallback_receiver"] = None

    def _ensure_fallback_receiver(self, info: dict) -> PicoscanReceiver | None:
        fb = info.get("fallback_receiver")
        if isinstance(fb, PicoscanReceiver) and bool(getattr(fb, "connected", False)):
            return fb
        now = time.time()
        next_retry = float(info.get("fallback_next_retry_ts", 0.0) or 0.0)
        if now < next_retry:
            return None

        formats = info.get("fallback_formats") or ["compact", "msgpack"]
        if not isinstance(formats, list) or len(formats) == 0:
            formats = ["compact", "msgpack"]
            info["fallback_formats"] = formats
        fmt_idx = int(info.get("fallback_format_idx", 0) or 0) % len(formats)
        fmt = str(formats[fmt_idx]).lower()
        listen_ip = str(info.get("fallback_listen_ip") or "0.0.0.0")
        listen_port = int(info.get("fallback_listen_port") or 2115)
        # Do not start fallback receiver if this UDP endpoint is already in use.
        for other_id, other_info in self.receivers.items():
            if other_info is info or not isinstance(other_info, dict):
                continue
            if not bool(other_info.get("listening", False)):
                continue
            # Primary picoscan listeners.
            if str(other_info.get("type") or "").lower() == "picoscan":
                other_ip = str(other_info.get("listen_ip") or "")
                other_port = int(other_info.get("listen_port") or 0)
                if other_ip == listen_ip and other_port == listen_port:
                    info["fallback_next_retry_ts"] = now + 10.0
                    info["fallback_block_reason"] = f"udp_endpoint_busy_by_{other_id}"
                    return None
            # Active fallback listeners in other receiver entries.
            other_fb = other_info.get("fallback_receiver")
            if isinstance(other_fb, PicoscanReceiver) and bool(getattr(other_fb, "connected", False)):
                other_ip = str(getattr(other_fb, "listen_ip", "") or "")
                other_port = int(getattr(other_fb, "listen_port", 0) or 0)
                if other_ip == listen_ip and other_port == listen_port:
                    info["fallback_next_retry_ts"] = now + 10.0
                    info["fallback_block_reason"] = f"fallback_udp_endpoint_busy_by_{other_id}"
                    return None

        fb = PicoscanReceiver(listen_ip=listen_ip, listen_port=listen_port, format_type=fmt)
        if fb.start_listening():
            info["fallback_receiver"] = fb
            info["fallback_active_format"] = fmt
            info["fallback_next_retry_ts"] = 0.0
            info["fallback_block_reason"] = None
            return fb
        err = str(getattr(fb, "last_error", "") or "")
        # On bind conflict, delay retries to avoid tight error loops.
        if "10048" in err:
            info["fallback_next_retry_ts"] = now + 10.0
            info["fallback_block_reason"] = "udp_bind_conflict"
        else:
            info["fallback_next_retry_ts"] = now + 2.0
        info["fallback_receiver"] = None
        return None

    def _rotate_fallback_format(self, info: dict):
        formats = info.get("fallback_formats") or ["compact", "msgpack"]
        if not isinstance(formats, list) or len(formats) == 0:
            formats = ["compact", "msgpack"]
            info["fallback_formats"] = formats
        idx = int(info.get("fallback_format_idx", 0) or 0)
        info["fallback_format_idx"] = (idx + 1) % len(formats)
        self._stop_fallback_receiver(info)

    def _try_scansegment_fallback_points(self, info: dict):
        if not bool(info.get("fallback_enabled", False)):
            return None

        fb = self._ensure_fallback_receiver(info)
        if fb is None:
            return None

        segments_to_request = info.get("segments_per_scan")
        if not segments_to_request:
            segments_to_request = int((device_manager.point_cloud_settings or {}).get("segments_per_scan") or 1)
        segments_to_request = max(1, int(segments_to_request))

        segments = fb.receive_segments(segments_to_request)
        if not segments:
            streak = int(info.get("fallback_no_data_streak", 0) or 0) + 1
            info["fallback_no_data_streak"] = streak
            if streak >= 15:
                info["fallback_no_data_streak"] = 0
                self._rotate_fallback_format(info)
            return None

        segment_payload = segments[0] if isinstance(segments, tuple) else segments
        segment_list = segment_payload if isinstance(segment_payload, list) else [segment_payload]
        self._update_segment_estimate_from_receive(info, segments, segment_list)
        assembled_segments = self._assemble_complete_frame_segments(info, segment_list)
        if not assembled_segments:
            return None

        pts = fb.segments_to_point_cloud(assembled_segments)
        if pts is not None and len(pts) > 0:
            info["fallback_no_data_streak"] = 0
            info["fallback_activations"] = int(info.get("fallback_activations", 0) or 0) + 1
            info["active_stream"] = f"scansegmentapi:{fb.format_type}"
            return pts
        return None

    def _worker_loop(self, device_id: str, stop_event: threading.Event):
        while not stop_event.is_set():
            info = self.receivers.get(device_id)
            if not isinstance(info, dict):
                time.sleep(0.05)
                continue
            if not info.get("listening"):
                time.sleep(0.05)
                continue
            now = time.time()
            next_poll_ts = float(info.get("next_poll_ts") or 0.0)
            if now < next_poll_ts:
                time.sleep(min(0.2, max(0.01, next_poll_ts - now)))
                continue
            receiver = info.get("receiver")
            if not receiver:
                info["availability"] = "offline"
                info["last_error"] = "receiver_missing"
                time.sleep(0.2)
                continue
            if not getattr(receiver, "connected", False):
                # Reconnect with increasing backoff
                try:
                    ok = bool(receiver.start_listening())
                except Exception:
                    ok = False
                if not ok:
                    info["availability"] = "offline"
                    info["last_error"] = "reconnect_failed"
                    info["error_streak"] = int(info.get("error_streak", 0)) + 1
                    backoff = min(5.0, 0.1 * (2 ** min(info["error_streak"], 6)))
                    info["poll_backoff_s"] = backoff
                    info["next_poll_ts"] = time.time() + backoff
                    time.sleep(min(0.25, backoff))
                    continue
            rtype = str(info.get("type") or "picoscan").lower()
            info_format = str(info.get("format_type") or "").lower()
            use_lmd_stream = (rtype == "lms4000") or (info_format == "lmdscandata")
            info["last_poll_ts"] = time.time()
            try:
                points = None
                if use_lmd_stream and hasattr(receiver, "receive_point_cloud"):
                    scans_to_request = info.get("segments_per_scan") or 2
                    points = receiver.receive_point_cloud(int(scans_to_request))
                    if points is not None and len(points) > 0:
                        info["active_stream"] = "lmdscandata"
                        if info.get("fallback_receiver") is not None:
                            self._stop_fallback_receiver(info)
                    else:
                        points = self._try_scansegment_fallback_points(info)
                else:
                    segments_to_request = info.get("segments_per_scan")
                    if not segments_to_request:
                        segments_to_request = int((device_manager.point_cloud_settings or {}).get("segments_per_scan") or 1)
                    segments_to_request = max(1, int(segments_to_request))
                    segments = receiver.receive_segments(segments_to_request)
                    if segments:
                        segment_payload = segments[0] if isinstance(segments, tuple) else segments
                        segment_list = segment_payload if isinstance(segment_payload, list) else [segment_payload]
                        self._update_segment_estimate_from_receive(info, segments, segment_list)
                        assembled_segments = self._assemble_complete_frame_segments(info, segment_list)
                        if assembled_segments:
                            points = receiver.segments_to_point_cloud(assembled_segments)
                            if points is not None and len(points) > 0:
                                info["active_stream"] = f"scansegmentapi:{getattr(receiver, 'format_type', 'compact')}"

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
                    info["availability"] = "online"
                    info["last_error"] = None
                    info["error_streak"] = 0
                    info["no_data_streak"] = 0
                    info["poll_backoff_s"] = 0.0
                    info["next_poll_ts"] = 0.0
                    data_ts = info.get("data_timestamps")
                    if isinstance(data_ts, deque):
                        data_ts.append(time.time())
                else:
                    streak = int(info.get("no_data_streak", 0)) + 1
                    info["no_data_streak"] = streak
                    if streak >= 20:
                        info["availability"] = "offline"
                    # dynamic backoff to limit unnecessary queries on missing data
                    backoff = min(1.0, 0.01 * streak)
                    info["poll_backoff_s"] = backoff
                    info["next_poll_ts"] = time.time() + backoff
                    time.sleep(min(0.2, backoff))
            except Exception as e:
                logger.debug("Receiver worker error [%s]: %s", device_id, e)
                info["availability"] = "offline"
                info["last_error"] = str(e)
                streak = int(info.get("error_streak", 0)) + 1
                info["error_streak"] = streak
                backoff = min(5.0, 0.1 * (2 ** min(streak, 6)))
                info["poll_backoff_s"] = backoff
                info["next_poll_ts"] = time.time() + backoff
                time.sleep(min(0.25, backoff))

    def _update_segment_estimate_from_receive(self, info: dict, receive_result, segment_list: list):
        try:
            frame_to_max_counter: dict[int, int] = {}
            # Preferred: vectors returned by ScanSegmentAPI.
            if isinstance(receive_result, tuple) and len(receive_result) >= 3:
                frame_numbers = receive_result[1] or []
                segment_counters = receive_result[2] or []
                for frame_no, seg_counter in zip(frame_numbers, segment_counters):
                    try:
                        f = int(frame_no)
                        c = int(seg_counter)
                    except Exception:
                        continue
                    if c < 0:
                        continue
                    prev = frame_to_max_counter.get(f)
                    if prev is None or c > prev:
                        frame_to_max_counter[f] = c

            # Fallback: read metadata from decoded segment payload.
            if not frame_to_max_counter:
                for seg in segment_list or []:
                    try:
                        modules = seg.get("Modules") if isinstance(seg, dict) else None
                        if not modules:
                            continue
                        m0 = modules[0]
                        f = int(m0.get("FrameNumber"))
                        c = int(m0.get("SegmentCounter"))
                        if c < 0:
                            continue
                        prev = frame_to_max_counter.get(f)
                        if prev is None or c > prev:
                            frame_to_max_counter[f] = c
                    except Exception:
                        continue

            if not frame_to_max_counter:
                return

            obs = info.get("segment_observations")
            if not isinstance(obs, deque):
                obs = deque(maxlen=120)
                info["segment_observations"] = obs

            for max_counter in frame_to_max_counter.values():
                # SegmentCounter is typically 0..N-1 => N = max + 1.
                seg_count = int(max_counter) + 1
                if seg_count <= 0 or seg_count > 128:
                    continue
                obs.append(seg_count)

            if len(obs) >= 3:
                arr = np.asarray(list(obs), dtype=np.float64)
                estimate = int(round(float(np.median(arr))))
                if estimate > 0:
                    info["segment_estimate"] = estimate
                    info["segment_estimate_updated_ts"] = time.time()
                    # Auto-use estimated value if no explicit per-device value exists.
                    if not info.get("segments_per_scan"):
                        info["segments_per_scan"] = estimate
        except Exception:
            pass

    def _extract_frame_counter(self, segment: dict):
        try:
            modules = segment.get("Modules") if isinstance(segment, dict) else None
            if not modules:
                return None, None
            m0 = modules[0]
            frame_no = m0.get("FrameNumber")
            seg_counter = m0.get("SegmentCounter")
            if frame_no is None or seg_counter is None:
                return None, None
            return int(frame_no), int(seg_counter)
        except Exception:
            return None, None

    def _assemble_complete_frame_segments(self, info: dict, segment_list: list):
        if not segment_list:
            return None
        point_cfg = device_manager.point_cloud_settings or {}
        strict_integrity = bool(point_cfg.get("require_complete_frames", True))
        stale_timeout_s = float(point_cfg.get("incomplete_frame_timeout_s", 0.35) or 0.35)

        # If frame metadata is unavailable, fallback to direct processing.
        parsed = []
        for seg in segment_list:
            frame_no, seg_counter = self._extract_frame_counter(seg)
            parsed.append((seg, frame_no, seg_counter))
        if any(f is None or c is None for _, f, c in parsed):
            return segment_list

        # Fullframe marker (-1) means complete scan in one segment.
        for seg, _, seg_counter in parsed:
            if seg_counter == -1:
                return [seg]

        pending = info.get("frame_segments_pending")
        pending_ts = info.get("frame_pending_ts")
        if not isinstance(pending, dict):
            pending = {}
            info["frame_segments_pending"] = pending
        if not isinstance(pending_ts, dict):
            pending_ts = {}
            info["frame_pending_ts"] = pending_ts

        now = time.time()
        frame_numbers_seen = []
        for seg, frame_no, seg_counter in parsed:
            if seg_counter < 0:
                continue
            frame_key = int(frame_no)
            frame_numbers_seen.append(frame_key)
            bucket = pending.get(frame_key)
            if not isinstance(bucket, dict):
                bucket = {}
                pending[frame_key] = bucket
            bucket[int(seg_counter)] = seg
            pending_ts[frame_key] = now

        expected = int(info.get("segments_per_scan") or 0)
        if expected <= 0:
            expected = int(info.get("segment_estimate") or 0)
        timeout_obs = info.get("segment_timeout_observations")
        if not isinstance(timeout_obs, deque):
            timeout_obs = deque(maxlen=40)
            info["segment_timeout_observations"] = timeout_obs

        # Prefer oldest pending frame to keep temporal order.
        for frame_key in sorted(pending.keys()):
            bucket = pending.get(frame_key) or {}
            if not bucket:
                continue
            if expected > 0:
                # If we already have at least expected segments, emit frame sorted by counter.
                # Do not require strict 0..N-1 continuity because hidden-angle segments may be omitted.
                if len(bucket) >= expected:
                    out = [bucket[idx] for idx in sorted(bucket.keys())]
                    pending.pop(frame_key, None)
                    pending_ts.pop(frame_key, None)
                    return out

        # If we already observed a newer frame, close oldest older frame.
        if frame_numbers_seen:
            max_seen_frame = max(frame_numbers_seen)
            last_seen = info.get("last_seen_frame_number")
            if isinstance(last_seen, int):
                max_seen_frame = max(max_seen_frame, last_seen)
            info["last_seen_frame_number"] = max_seen_frame
            older_frames = [fk for fk in pending.keys() if int(fk) < int(max_seen_frame)]
            if older_frames:
                frame_key = min(older_frames)
                bucket = pending.get(frame_key) or {}
                if bucket:
                    if strict_integrity and expected > 0 and len(bucket) < expected:
                        info["incomplete_frames_dropped"] = int(info.get("incomplete_frames_dropped", 0) or 0) + 1
                        timeout_obs.append(len(bucket))
                        pending.pop(frame_key, None)
                        pending_ts.pop(frame_key, None)
                        return None
                    out = [bucket[idx] for idx in sorted(bucket.keys())]
                    pending.pop(frame_key, None)
                    pending_ts.pop(frame_key, None)
                    return out

        # Cleanup old incomplete frames and use them to auto-correct expected segments.
        for frame_key, ts in list(pending_ts.items()):
            if (now - float(ts or now)) < stale_timeout_s:
                continue
            bucket = pending.get(frame_key) or {}
            counters = sorted(int(k) for k in bucket.keys())
            contiguous = bool(counters) and counters == list(range(counters[-1] + 1))
            if contiguous:
                timeout_obs.append(len(counters))
            if bucket:
                if strict_integrity and expected > 0 and len(bucket) < expected:
                    info["incomplete_frames_dropped"] = int(info.get("incomplete_frames_dropped", 0) or 0) + 1
                    pending.pop(frame_key, None)
                    pending_ts.pop(frame_key, None)
                    return None
                out = [bucket[idx] for idx in counters]
                pending.pop(frame_key, None)
                pending_ts.pop(frame_key, None)
                return out
            pending.pop(frame_key, None)
            pending_ts.pop(frame_key, None)

        # If config is too high and stale frames repeatedly show lower contiguous sizes,
        # adapt runtime expected count (without immediate config file write).
        if len(timeout_obs) >= 8:
            inferred = int(round(float(np.median(np.asarray(list(timeout_obs), dtype=np.float64)))))
            if inferred > 0 and inferred <= 128 and expected > 0 and abs(inferred - expected) >= 2:
                logger.info(
                    "Adaptive segments_per_scan update for %s: %s -> %s (from incomplete-frame observations)",
                    info.get("device_id"),
                    expected,
                    inferred,
                )
                info["segments_per_scan"] = inferred
                info["segment_estimate"] = inferred
                info["segment_estimate_updated_ts"] = now

        # Keep pending map bounded.
        if len(pending) > 8:
            for frame_key in sorted(pending.keys())[:-8]:
                pending.pop(frame_key, None)
                pending_ts.pop(frame_key, None)
        return None

    def get_latest_point_cloud(self, device_id: str):
        info = self.receivers.get(device_id)
        if not isinstance(info, dict):
            return None
        lock = info.get("lock")
        pts = None
        frame = int(info.get("frame_counter", 0))
        ts = info.get("latest_update_ts")
        if ts is not None:
            # Avoid returning stale clouds when device is effectively unavailable.
            if (time.time() - float(ts)) > 2.5:
                return None
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

    def get_health_snapshot(self) -> dict:
        snapshot: dict = {}
        now = time.time()
        for device_id, info in self.receivers.items():
            if not isinstance(info, dict):
                continue
            device_cfg = device_manager.get_device(device_id)
            per_device_segments = getattr(device_cfg, "segments_per_scan", None) if device_cfg else None
            global_default_segments = int((device_manager.point_cloud_settings or {}).get("segments_per_scan") or 1)
            runtime_segments = info.get("segments_per_scan")
            effective_segments = (
                runtime_segments
                if runtime_segments is not None
                else (per_device_segments if per_device_segments is not None else global_default_segments)
            )
            data_ts = info.get("data_timestamps")
            data_hz = None
            if isinstance(data_ts, deque) and len(data_ts) >= 2:
                dt = float(data_ts[-1] - data_ts[0])
                if dt > 0:
                    data_hz = float((len(data_ts) - 1) / dt)
            latest_ts = info.get("latest_update_ts")
            age_s = (now - latest_ts) if latest_ts else None
            snapshot[device_id] = {
                "device_id": device_id,
                "type": info.get("type"),
                "active_stream": info.get("active_stream"),
                "listening": bool(info.get("listening", False)),
                "connected": bool(getattr(info.get("receiver"), "connected", False)),
                "availability": info.get("availability", "unknown"),
                "last_error": info.get("last_error"),
                "error_streak": int(info.get("error_streak", 0) or 0),
                "no_data_streak": int(info.get("no_data_streak", 0) or 0),
                "latest_data_age_s": age_s,
                "data_rate_hz": data_hz,
                "segments_per_scan_configured": per_device_segments,
                "segments_per_scan_global_default": global_default_segments,
                "segments_per_scan_runtime": runtime_segments,
                "segments_per_scan_effective": effective_segments,
                "segments_per_scan_estimated": info.get("segment_estimate"),
                "segments_estimate_samples": len(info.get("segment_observations") or []),
                "segments_estimate_updated_ts": info.get("segment_estimate_updated_ts"),
                "incomplete_frames_dropped": int(info.get("incomplete_frames_dropped", 0) or 0),
                "fallback_enabled": bool(info.get("fallback_enabled", False)),
                "fallback_active_format": info.get("fallback_active_format"),
                "fallback_activations": int(info.get("fallback_activations", 0) or 0),
                "fallback_block_reason": info.get("fallback_block_reason"),
                "fallback_retry_in_s": max(0.0, float(info.get("fallback_next_retry_ts", 0.0) or 0.0) - now),
            }
        return snapshot

    def start_auto_recovery(self, devices_supplier, interval_s: float = 2.0):
        if self._auto_thread and self._auto_thread.is_alive():
            return
        self._auto_stop = threading.Event()
        self._auto_thread = threading.Thread(
            target=self._auto_recovery_loop,
            args=(devices_supplier, max(0.5, float(interval_s))),
            daemon=True,
        )
        self._auto_thread.start()

    def stop_auto_recovery(self):
        if self._auto_stop:
            self._auto_stop.set()
        if self._auto_thread and self._auto_thread.is_alive():
            self._auto_thread.join(timeout=1.5)
        self._auto_thread = None
        self._auto_stop = None

    def _auto_recovery_loop(self, devices_supplier, interval_s: float):
        while self._auto_stop and not self._auto_stop.is_set():
            try:
                devices = list(devices_supplier() or [])
                enabled_ids = {d.device_id for d in devices if bool(getattr(d, "enabled", True))}
                # Stop listeners for disabled/removed devices
                for device_id in list(self.receivers.keys()):
                    if device_id not in enabled_ids:
                        self.stop_listening(device_id)

                now = time.time()
                for d in devices:
                    if not bool(getattr(d, "enabled", True)):
                        continue
                    device_id = d.device_id
                    receiver_info = self.receivers.get(device_id)
                    needs_restart = False
                    if isinstance(receiver_info, dict):
                        worker = self._worker_threads.get(device_id)
                        worker_alive = bool(worker and worker.is_alive())
                        listening = bool(receiver_info.get("listening", False))
                        receiver = receiver_info.get("receiver")
                        connected = bool(getattr(receiver, "connected", False))
                        # Recover if worker died, listener is marked off, or transport dropped.
                        if (not worker_alive) or (not listening) or (not connected):
                            needs_restart = True
                    elif receiver_info is not None:
                        # Legacy/non-dict receiver entry - normalize by restart.
                        needs_restart = True

                    if needs_restart:
                        try:
                            self.stop_listening(device_id)
                        except Exception:
                            pass
                        receiver_info = None

                    if receiver_info is not None:
                        continue

                    state = self._auto_retry_state.get(device_id, {"attempts": 0, "next_ts": 0.0})
                    if now < float(state.get("next_ts", 0.0)):
                        continue
                    ok = self.start_listening(
                        d.device_id,
                        "0.0.0.0",
                        d.port,
                        segments_per_scan=getattr(d, "segments_per_scan", None),
                        format_type=getattr(d, "format_type", "compact"),
                        device_type=getattr(d, "device_type", "picoscan"),
                        sensor_ip=getattr(d, "ip_address", None),
                    )
                    if ok:
                        self._auto_retry_state.pop(device_id, None)
                    else:
                        attempts = int(state.get("attempts", 0)) + 1
                        backoff = min(30.0, 1.0 * (2 ** min(attempts, 5)))
                        self._auto_retry_state[device_id] = {"attempts": attempts, "next_ts": now + backoff}
            except Exception as exc:
                logger.debug("auto_recovery_loop error: %s", exc)
            time.sleep(interval_s)
