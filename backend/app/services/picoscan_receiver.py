"""
Picoscan Receiver - Nasłuchiwanie danych UDP od Picoscanu
PC jest SERWEREM, Picoscan wysyła dane UDP
"""
import builtins
import logging
import re
import socket
from collections import deque
from typing import Optional, List, Tuple
import numpy as np
import threading
import time
from app.services.lms4000_receiver import Lms4000Receiver
from app.core.device_manager import device_manager
from app.services.udp_ingress import ProcessUDPHandler
from app.services.compact_numpy import read_beam_data
from app.services.scan_logging import quiet_segment_progress
from app.services.scan_process import ScanProcessReceiver

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


def _scansegment_print(*args, **kwargs):
    """Quiet only the library's per-read progress message, not diagnostics."""
    if (not kwargs and len(args) == 1 and isinstance(args[0], str)
            and re.fullmatch(r"Received segment [0-9]+\.", args[0])):
        logger.debug("%s", args[0])
        return
    return builtins.print(*args, **kwargs)


# Module globals affect only these libraries' bare print calls. Never redirect
# process stdout or hold a shared lock across independent blocking socket reads.
CompactApi.print = _scansegment_print
CompactApi._read_beam_data = read_beam_data
MsgpackApi.print = _scansegment_print
quiet_segment_progress(CompactApi)
quiet_segment_progress(MsgpackApi)

UDP_RCVBUF_BYTES = 4 * 1024 * 1024
PATTERN_OBSERVATION_WINDOW = 40
PIPELINE_TIMING_WINDOW = 120
CAPTURE_QUEUE_MAX = 256
PATTERN_LOCK_FRAMES = 5


class _TimestampedLmdReceiver(Lms4000Receiver):
    """Capture host receipt of an assembled telegram before point conversion.

    This is not first TCP-byte arrival or scanner measurement time. The lower
    API buffers TCP reads, so socket-read timestamps are not exposed here.
    """

    def _parse_lmdscandata(self, telegram):
        self.last_frame_receipt = {
            "timestamp": time.time(), "received_monotonic": time.monotonic(),
            "timestamp_source": "host_telegram_preparse", "device_frame": None,
        }
        return super()._parse_lmdscandata(telegram)


class _TimestampedUDPHandler(UDPHandler):
    """Timestamp host socket reads, not scanner/device measurement time."""

    def receive_new_scan_segment(self):
        result = super().receive_new_scan_segment()
        if result[0]:
            self.last_host_receipt = (time.time(), time.monotonic())
        return result


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
                if self.transport_layer is not None:
                    self.transport_layer.close()
                self.transport_layer = ProcessUDPHandler(
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
                if self.transport_layer:
                    self.transport_layer.close()
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

                # Do not redirect process-global stdout or serialize independent sockets.
                # Read individually so parse failures cannot misalign timestamps.
                segments, frames, counters = [], [], []
                for _ in range(num_segments):
                    self.transport_layer.last_host_receipt = None
                    try:
                        batch = self.receiver.receive_segments(1)
                    except TimeoutError:
                        break
                    except Exception:
                        if not segments:
                            raise
                        logger.exception("Segment read failed after a partial batch; preserving prior segments")
                        break
                    receipt = self.transport_layer.last_host_receipt
                    if batch and batch[0]:
                        for segment in batch[0]:
                            if receipt is not None:
                                segment["_host_received_wall"], segment["_host_received_monotonic"] = receipt
                        segments.extend(batch[0])
                        frames.extend(batch[1])
                        counters.extend(batch[2])
                result = (segments, frames, counters)

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
                        self.last_receive_ts = segments[-1].get("_host_received_wall", time.time())
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
            "last_receive_ts": self.last_receive_ts,
            "ingress": self.transport_layer.get_metrics() if self.transport_layer else {}
        }
    
    def segments_to_point_cloud(self, segments: List[dict]) -> np.ndarray:
        """Convert segments to point cloud (X, Y, Z, RSSI)."""
        arrays: list[np.ndarray] = []

        try:
            for segment in segments:
                if self.format_type == "msgpack":
                    segment_points = self._extract_points_msgpack(segment)
                else:
                    segment_points = self._extract_points_compact(segment)
                if isinstance(segment_points, np.ndarray) and segment_points.ndim == 2 and segment_points.shape[0] > 0:
                    arrays.append(segment_points.astype(np.float32, copy=False))

            if arrays:
                return np.vstack(arrays).astype(np.float32, copy=False)
            return np.array([], dtype=np.float32).reshape(0, 4)
        except Exception as e:
            logger.error(f"Error converting: {e}")
            return np.array([], dtype=np.float32).reshape(0, 4)

    def _build_points_from_scan(
        self,
        distances,
        *,
        phi: float,
        theta_start: float,
        theta_stop: float,
        channel_theta,
        rssi,
    ) -> np.ndarray:
        distance_arr = np.asarray(distances, dtype=np.float32)
        if distance_arr.ndim != 2 or distance_arr.shape[0] == 0 or distance_arr.shape[1] == 0:
            return np.empty((0, 4), dtype=np.float32)

        num_beams = int(distance_arr.shape[1])
        if channel_theta is not None:
            theta_arr = np.asarray(channel_theta, dtype=np.float32)
            if theta_arr.ndim != 1 or theta_arr.shape[0] != num_beams:
                theta_arr = None
        else:
            theta_arr = None

        if theta_arr is None:
            if num_beams > 1:
                theta_arr = np.linspace(theta_start, theta_stop, num_beams, dtype=np.float32)
            else:
                theta_arr = np.asarray([theta_start], dtype=np.float32)

        valid_rows, valid_cols = np.nonzero(distance_arr > 0)
        if valid_rows.size == 0:
            return np.empty((0, 4), dtype=np.float32)

        distance_vals = distance_arr[valid_rows, valid_cols]
        theta_vals = theta_arr[valid_cols]
        phi_val = np.float32(phi)
        cos_theta = np.cos(theta_vals).astype(np.float32, copy=False)
        sin_theta = np.sin(theta_vals).astype(np.float32, copy=False)
        cos_phi = np.float32(np.cos(phi_val))
        sin_phi = np.float32(np.sin(phi_val))

        x = distance_vals * cos_theta * cos_phi
        y = distance_vals * cos_theta * sin_phi
        z = distance_vals * sin_theta

        rssi_vals = np.zeros_like(distance_vals, dtype=np.float32)
        try:
            rssi_arr = np.asarray(rssi, dtype=np.float32)
            if rssi_arr.shape == distance_arr.shape:
                rssi_vals = rssi_arr[valid_rows, valid_cols]
        except Exception:
            pass

        return np.column_stack((x, y, z, rssi_vals)).astype(np.float32, copy=False)

    def _extract_points_compact(self, segment: dict) -> np.ndarray:
        """Extract points from Compact format segment."""
        arrays: list[np.ndarray] = []

        try:
            for module in segment.get("Modules", []):
                phi_list = module.get("Phi", [0.0])
                theta_start_list = module.get("ThetaStart", [0.0])
                theta_stop_list = module.get("ThetaStop", [0.0])

                for scan_idx, scan in enumerate(module.get("SegmentData", [])):
                    phi = phi_list[scan_idx] if scan_idx < len(phi_list) else 0.0
                    theta_start = theta_start_list[scan_idx] if scan_idx < len(theta_start_list) else 0.0
                    theta_stop = theta_stop_list[scan_idx] if scan_idx < len(theta_stop_list) else theta_start
                    scan_points = self._build_points_from_scan(
                        scan.get("Distance", []),
                        phi=phi,
                        theta_start=theta_start,
                        theta_stop=theta_stop,
                        channel_theta=scan.get("ChannelTheta"),
                        rssi=scan.get("Rssi"),
                    )
                    if scan_points.shape[0] > 0:
                        arrays.append(scan_points)
        except Exception as e:
            logger.warning(f"Error extracting points: {e}")

        if not arrays:
            return np.empty((0, 4), dtype=np.float32)
        return np.vstack(arrays).astype(np.float32, copy=False)

    def _extract_points_msgpack(self, segment: dict) -> np.ndarray:
        """Extract points from msgpack format segment."""
        arrays: list[np.ndarray] = []

        try:
            for scan in segment.get("SegmentData", []):
                scan_points = self._build_points_from_scan(
                    scan.get("Distance", []),
                    phi=scan.get("Phi", 0.0),
                    theta_start=scan.get("ThetaStart", 0.0),
                    theta_stop=scan.get("ThetaStop", scan.get("ThetaStart", 0.0)),
                    channel_theta=scan.get("ChannelTheta"),
                    rssi=scan.get("Rssi"),
                )
                if scan_points.shape[0] > 0:
                    arrays.append(scan_points)
        except Exception as e:
            logger.warning(f"Error extracting msgpack points: {e}")

        if not arrays:
            return np.empty((0, 4), dtype=np.float32)
        return np.vstack(arrays).astype(np.float32, copy=False)
    
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

    def _build_receiver_info(
        self,
        *,
        receiver,
        receiver_type: str,
        device_id: str,
        device_type: str,
        format_type: str,
        listen_ip: str,
        listen_port: int,
        segments_per_scan: int | None,
        fallback_enabled: bool,
        fallback_listen_ip: str | None,
        fallback_listen_port: int | None,
        fallback_formats: list[str],
        segment_pattern: list[int] | None = None,
    ) -> dict:
        if segments_per_scan is None:
            segments_per_scan = int((device_manager.point_cloud_settings or {}).get("segments_per_scan") or 10)
        # Counters identify angular sectors; partial FOV need not start at zero.
        expected_pattern = None
        if segment_pattern is not None:
            expected_pattern = sorted(set(segment_pattern))
            if (not expected_pattern or len(expected_pattern) != segments_per_scan
                    or any(type(c) is not int or c < 0 for c in expected_pattern)):
                raise ValueError("segment_pattern must contain segments_per_scan distinct nonnegative integers")
        return {
            "receiver": receiver,
            "type": receiver_type,
            "device_id": device_id,
            "device_type": device_type,
            "format_type": format_type,
            "listening": True,
            "listen_ip": listen_ip,
            "listen_port": listen_port,
            "segments": [],
            "segments_per_scan_hint": segments_per_scan,
            "expected_segment_pattern": expected_pattern,
            "dominant_segment_pattern": expected_pattern,
            "dominant_pattern_samples": 0,
            "pattern_observations": deque(maxlen=PATTERN_OBSERVATION_WINDOW),
            "pattern_switch_count": 0,
            "pattern_last_switch_ts": None,
            "last_closed_frame_number": None,
            "last_closed_segment_pattern": [],
            "last_complete_frame_number": None,
            "last_complete_segment_pattern": [],
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
            "frame_segments_pending": {},
            "frame_pending_ts": {},
            "incomplete_frames_dropped": 0,
            "capture_queue": deque(),
            "capture_frame_counter": 0,
            "queue_overflows": 0,
            "duplicate_segments": 0,
            "late_complete_frames_dropped": 0,
            "device_frame_gaps": 0,
            "device_frame_regressions": 0,
            "pattern_learning_frames_dropped": 0,
            "pattern_locked": expected_pattern is not None,
            "device_frame_tracking_supported": receiver_type == "picoscan",
            "last_seen_frame_number": None,
            "last_poll_ts": None,
            "active_stream": (
                "lmdscandata"
                if str(receiver_type).lower() == "lmdscandata"
                else f"scansegmentapi:{format_type}"
            ),
            "fallback_enabled": fallback_enabled,
            "fallback_receiver": None,
            "fallback_listen_ip": fallback_listen_ip,
            "fallback_listen_port": fallback_listen_port,
            "fallback_formats": fallback_formats,
            "fallback_format_idx": 0,
            "fallback_active_format": None,
            "fallback_no_data_streak": 0,
            "fallback_activations": 0,
            "fallback_next_retry_ts": 0.0,
            "fallback_block_reason": None,
            "last_partial_frame_number": None,
            "last_partial_segment_counters": [],
            "last_segment_stats": None,
            "perf_udp_receive_ms": deque(maxlen=PIPELINE_TIMING_WINDOW),
            "perf_assemble_ms": deque(maxlen=PIPELINE_TIMING_WINDOW),
            "perf_convert_ms": deque(maxlen=PIPELINE_TIMING_WINDOW),
            "perf_store_ms": deque(maxlen=PIPELINE_TIMING_WINDOW),
            "perf_cycle_ms": deque(maxlen=PIPELINE_TIMING_WINDOW),
            "perf_last_points_count": 0,
        }

    def _record_timing(self, info: dict, key: str, duration_ms: float):
        samples = info.get(key)
        if not isinstance(samples, deque):
            samples = deque(maxlen=PIPELINE_TIMING_WINDOW)
            info[key] = samples
        try:
            samples.append(float(duration_ms))
        except Exception:
            pass

    def _summarize_timing(self, samples) -> dict | None:
        if not isinstance(samples, deque) or len(samples) == 0:
            return None
        arr = np.asarray(list(samples), dtype=np.float64)
        if arr.size == 0:
            return None
        return {
            "samples": int(arr.size),
            "last_ms": float(arr[-1]),
            "mean_ms": float(np.mean(arr)),
            "p95_ms": float(np.percentile(arr, 95)),
        }

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
        segment_pattern: list[int] | None = None,
    ) -> bool:
        """Start a listener with an optional exact transmitted sector pattern.

        Otherwise learn a frozen pattern of segments_per_scan counters. Neither
        mode verifies the scanner configuration; configure the correct count.
        """
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
                receiver = _TimestampedLmdReceiver(sensor_ip=sensor_ip, sensor_port=listen_port)
                ok = receiver.start_listening()
                if ok:
                    logger.info("Listening for LMDscandata on TCP %s:%s (%s)", sensor_ip, listen_port, device_id)
                    self.receivers[device_id] = self._build_receiver_info(
                        receiver=receiver,
                        receiver_type="lmdscandata",
                        device_id=device_id,
                        device_type=dev_type,
                        format_type="lmdscandata",
                        listen_ip=sensor_ip,
                        listen_port=listen_port,
                        segments_per_scan=segments_per_scan,
                        segment_pattern=segment_pattern,
                        fallback_enabled=True,
                        fallback_listen_ip="0.0.0.0",
                        fallback_listen_port=2115 if int(listen_port) == 2111 else int(listen_port),
                        fallback_formats=["compact", "msgpack"],
                    )
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

            process_config = dict(receiver_type="picoscan", device_id=device_id,
                device_type=dev_type, format_type=fmt, listen_ip=listen_ip,
                listen_port=listen_port, segments_per_scan=segments_per_scan,
                segment_pattern=segment_pattern, fallback_enabled=False,
                fallback_listen_ip=None, fallback_listen_port=None, fallback_formats=[])
            receiver = ScanProcessReceiver(process_config, device_manager.point_cloud_settings)
            if receiver.start_listening():
                self.receivers[device_id] = self._build_receiver_info(
                    receiver=receiver,
                    receiver_type="picoscan",
                    device_id=device_id,
                    device_type=dev_type,
                    format_type=receiver.format_type,
                    listen_ip=listen_ip,
                    listen_port=listen_port,
                    segments_per_scan=segments_per_scan,
                    segment_pattern=segment_pattern,
                    fallback_enabled=False,
                    fallback_listen_ip=None,
                    fallback_listen_port=None,
                    fallback_formats=[],
                )
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
        receiver = self.receivers[device_id].get("receiver")
        target = self._process_bridge_loop if hasattr(receiver, "receive_packet") else self._worker_loop
        worker = threading.Thread(target=target, args=(device_id, stop_event), daemon=True)
        self._worker_threads[device_id] = worker
        worker.start()

    def _process_bridge_loop(self, device_id, stop_event):
        info = self.receivers[device_id]
        receiver = info['receiver']
        while not stop_event.is_set():
            packet = receiver.receive_packet()
            if packet is None:
                if not receiver.connected:
                    info['availability'] = 'offline'
                    info['last_error'] = receiver.last_error
                    break
                continue
            info['process_health'] = packet['health']
            info['process_health_received'] = time.time()
            info['last_segment_stats'] = packet['stats']
            for frame in packet['frames']:
                self._enqueue_point_cloud(info, frame['points'], frame)
                with info['lock']:
                    info['latest_points'] = frame['points']
                    info['latest_update_ts'] = frame['timestamp']
                    info['frame_counter'] += 1
            info['availability'] = packet['health']['availability']

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

        # Read small batches from UDP to minimize blocking latency.
        # Full-frame assembly is handled separately by segments_per_scan logic.
        batch_segments = int((device_manager.point_cloud_settings or {}).get("receive_batch_segments") or 1)
        batch_segments = max(1, min(16, batch_segments))

        segments = fb.receive_segments(batch_segments)
        if not segments:
            points = self._convert_completed_frames(info, fb, [])
            if points is not None:
                return points
            streak = int(info.get("fallback_no_data_streak", 0) or 0) + 1
            info["fallback_no_data_streak"] = streak
            if streak >= 15:
                info["fallback_no_data_streak"] = 0
                self._rotate_fallback_format(info)
            return None

        segment_payload = segments[0] if isinstance(segments, tuple) else segments
        segment_list = segment_payload if isinstance(segment_payload, list) else [segment_payload]
        pts = self._convert_completed_frames(info, fb, segment_list)
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
                cycle_start = time.perf_counter()
                points = None
                had_transport_activity = False
                if use_lmd_stream and hasattr(receiver, "receive_point_cloud"):
                    # A single LMD telegram is a whole scan, never merge scans
                    # with different receipt times into one acquisition frame.
                    receiver.last_frame_receipt = None
                    points = receiver.receive_point_cloud(1)
                    if points is not None and len(points) > 0:
                        receipt = receiver.last_frame_receipt or {
                            "timestamp": time.time(), "received_monotonic": time.monotonic(),
                            "timestamp_source": "host_api_return", "device_frame": None,
                        }
                        self._enqueue_point_cloud(info, points, receipt)
                        info["active_stream"] = "lmdscandata"
                        if info.get("fallback_receiver") is not None:
                            self._stop_fallback_receiver(info)
                    else:
                        points = self._try_scansegment_fallback_points(info)
                else:
                    # Keep UDP receive non-blocking-ish: batch size controls read cadence,
                    # while segments_per_scan still controls complete-frame assembly.
                    batch_segments = int((device_manager.point_cloud_settings or {}).get("receive_batch_segments") or 1)
                    batch_segments = max(1, min(16, batch_segments))
                    t_udp = time.perf_counter()
                    segments = receiver.receive_segments(batch_segments)
                    if not segments:
                        points = self._convert_completed_frames(info, receiver, [])
                    self._record_timing(info, "perf_udp_receive_ms", (time.perf_counter() - t_udp) * 1000.0)
                    if segments:
                        had_transport_activity = True
                        segment_payload = segments[0] if isinstance(segments, tuple) else segments
                        segment_list = segment_payload if isinstance(segment_payload, list) else [segment_payload]
                        info["last_segment_stats"] = self._summarize_segment_payload(segment_list)
                        points = self._convert_completed_frames(info, receiver, segment_list)
                        if points is not None:
                            if points is not None and len(points) > 0:
                                info["active_stream"] = f"scansegmentapi:{getattr(receiver, 'format_type', 'compact')}"
                                info["last_partial_frame_number"] = None
                                info["last_partial_segment_counters"] = []
                        else:
                            pending = info.get("frame_segments_pending") or {}
                            if pending:
                                newest_frame = max(int(frame_no) for frame_no in pending.keys())
                                newest_parts = pending.get(newest_frame) or {}
                                info["last_partial_frame_number"] = newest_frame
                                info["last_partial_segment_counters"] = sorted(int(seg_no) for seg_no in newest_parts.keys())

                if points is not None and len(points) > 0:
                    t_store = time.perf_counter()
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
                    self._record_timing(info, "perf_store_ms", (time.perf_counter() - t_store) * 1000.0)
                    info["perf_last_points_count"] = int(len(points))
                    info["availability"] = "online"
                    info["last_error"] = None
                    info["error_streak"] = 0
                    info["no_data_streak"] = 0
                    info["poll_backoff_s"] = 0.0
                    info["next_poll_ts"] = 0.0
                    data_ts = info.get("data_timestamps")
                    if isinstance(data_ts, deque):
                        data_ts.append(time.time())
                elif had_transport_activity:
                    info["availability"] = "online"
                    info["last_error"] = None
                    info["error_streak"] = 0
                    info["no_data_streak"] = 0
                    info["poll_backoff_s"] = 0.0
                    info["next_poll_ts"] = 0.0
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
                self._record_timing(info, "perf_cycle_ms", (time.perf_counter() - cycle_start) * 1000.0)
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

    def _normalize_segment_pattern(self, counters: list[int] | tuple[int, ...] | None) -> tuple[int, ...]:
        if not counters:
            return tuple()
        try:
            return tuple(sorted({int(c) for c in counters}))
        except Exception:
            return tuple()

    def _extract_frame_counter(self, segment: dict):
        try:
            modules = segment.get("Modules")
            metadata = modules[0] if modules else segment
            frame, counter = metadata.get("FrameNumber"), metadata.get("SegmentCounter")
            if frame is None or counter is None:
                return None, None
            if (isinstance(frame, bool) or isinstance(counter, bool)
                    or int(frame) != frame or int(counter) != counter or int(frame) < 0):
                return None, None
            return int(frame), int(counter)
        except (AttributeError, TypeError, ValueError, IndexError, OverflowError):
            return None, None

    def _assemble_complete_frame_segments(self, info: dict, segment_list: list):
        """Assemble configured-count scans, never infer a smaller count.

        Without an explicit pattern, wait for timeout-closed frames and lock
        after five matching, exactly configured-count patterns. Lock is frozen
        until restart. This cannot prove scanner configuration: the operator
        must configure the correct count for the scanner's transmitted FOV.
        """
        timeout = float((device_manager.point_cloud_settings or {}).get(
            "incomplete_frame_timeout_s", 0.35) or 0.35)
        count = int(info.get("segments_per_scan_hint") or 0)
        pending = info.setdefault("frame_segments_pending", {})
        receipts = info.setdefault("frame_pending_ts", {})
        closed = info.setdefault("closed_frames", deque(maxlen=4096))
        closed_index = info.get("closed_frame_index")
        if closed_index is None:
            closed_index = info["closed_frame_index"] = set(closed)
        completed = []

        def close(frame, invalid=False):
            bucket = pending.pop(frame)
            wall, monotonic = receipts.pop(frame)
            if len(closed) == closed.maxlen:
                closed_index.discard(closed[0])
            closed.append(frame)
            closed_index.add(frame)
            pattern = sorted(bucket)
            expected = info.get("expected_segment_pattern")
            full = pattern == [-1]
            valid = not invalid and (full or (count > 0 and len(pattern) == count))
            if valid and not full and expected is None:
                candidate = info.get("pattern_candidate")
                samples = info.get("pattern_candidate_samples", 0) + 1 if candidate == pattern else 1
                info["pattern_candidate"] = pattern
                info["pattern_candidate_samples"] = samples
                if samples >= PATTERN_LOCK_FRAMES:
                    expected = pattern
                    info["expected_segment_pattern"] = pattern
                    info["pattern_locked"] = True
                else:
                    info["pattern_learning_frames_dropped"] += 1
                    return
            elif not valid and not info.get("pattern_locked"):
                info["pattern_candidate_samples"] = 0
            valid = valid and (full or pattern == expected)
            info["last_closed_frame_number"] = frame
            info["last_closed_segment_pattern"] = pattern
            if not valid:
                info["incomplete_frames_dropped"] += 1
                info["last_partial_frame_number"] = frame
                info["last_partial_segment_counters"] = pattern
                return
            info["last_complete_frame_number"] = frame
            info["last_complete_segment_pattern"] = pattern
            completed.append({
                "segments": [bucket[k] for k in pattern],
                "timestamp": wall, "received_monotonic": monotonic,
                "timestamp_source": "host_socket_read",
                "device_frame": frame, "complete": True,
            })

        def expire(stamp):
            learning = info.get("expected_segment_pattern") is None
            for frame in list(pending):
                if stamp - receipts[frame][1] >= timeout:
                    close(frame)
            expected = info.get("expected_segment_pattern")
            if learning and expected is not None:
                # Frames buffered while learning are already complete. Release
                # them before a newer frame can overtake them and make them late.
                for frame in sorted(pending, key=lambda f: receipts[f][1]):
                    if sorted(pending[frame]) == expected:
                        close(frame)

        for segment in segment_list:
            # Expire in receipt-time order, not parsing/batch-return time. A slow
            # parser must not discard segments read before the frame deadline.
            if not isinstance(segment, dict):
                info["incomplete_frames_dropped"] += 1
                continue
            receipt = (segment.get("_host_received_wall", time.time()),
                       segment.get("_host_received_monotonic", time.monotonic()))
            expire(receipt[1])
            frame, counter = self._extract_frame_counter(segment)
            if frame is None:
                info["incomplete_frames_dropped"] += 1
                continue
            if frame in closed_index:
                info["duplicate_segments"] += 1
                continue
            bucket = pending.setdefault(frame, {})
            receipts.setdefault(frame, receipt)
            if counter in bucket:
                info["duplicate_segments"] += 1
                continue
            bucket[counter] = segment
            expected = info.get("expected_segment_pattern")
            if (counter < -1 or (counter != -1 and count <= 0)
                    or (expected is not None and counter != -1 and counter not in expected)
                    or (count > 0 and len(bucket) > count)):
                close(frame, invalid=True)
            elif counter == -1 or (expected is not None and sorted(bucket) == expected):
                close(frame)
            while len(pending) > CAPTURE_QUEUE_MAX:
                close(next(iter(pending)), invalid=True)
        # Do not expire buffered frames against parser time while queued packets
        # still carry earlier receipt times. Empty reads advance the watermark.
        expire(receipt[1] if segment_list else time.monotonic())
        return sorted(completed, key=lambda frame: frame["received_monotonic"])

    def _enqueue_point_cloud(self, info: dict, points, receipt: dict):
        """Queue owns its points; draining transfers ownership to acquisition."""
        with info["lock"]:
            stamp = receipt["received_monotonic"]
            if stamp < info.get("last_queued_monotonic", float("-inf")):
                info["late_complete_frames_dropped"] += 1
                return False
            device_frame = receipt.get("device_frame")
            previous = info.get("last_queued_device_frame")
            if device_frame is not None and previous is not None:
                if device_frame <= previous:
                    # Includes scanner resets: restart listener to establish a
                    # new device epoch rather than silently accepting a rewind.
                    info["device_frame_regressions"] += 1
                    info["late_complete_frames_dropped"] += 1
                    return False
                info["device_frame_gaps"] += max(0, device_frame - previous - 1)
            info["last_queued_monotonic"] = stamp
            info["last_queued_device_frame"] = device_frame
            info["device_frame_tracking_supported"] = device_frame is not None
            queue = info.setdefault("capture_queue", deque())
            if len(queue) >= CAPTURE_QUEUE_MAX:
                queue.popleft()
                info["queue_overflows"] = info.get("queue_overflows", 0) + 1
            frame = info.get("capture_frame_counter", 0) + 1
            info["capture_frame_counter"] = frame
            queue.append({
                "frame": frame, "points": np.array(points, copy=True),
                "device_frame": device_frame,
                "timestamp": receipt["timestamp"],
                "received_monotonic": receipt["received_monotonic"],
                "timestamp_source": receipt.get("timestamp_source", "host_api_return"),
                "complete": True,
            })
            return True

    def _convert_completed_frames(self, info: dict, receiver, segment_list: list):
        points = None
        started = time.perf_counter()
        frames = self._assemble_complete_frame_segments(info, segment_list)
        self._record_timing(info, "perf_assemble_ms", (time.perf_counter() - started) * 1000.0)
        for frame in frames:
            started = time.perf_counter()
            converted = receiver.segments_to_point_cloud(frame["segments"])
            self._record_timing(info, "perf_convert_ms", (time.perf_counter() - started) * 1000.0)
            if converted is not None:
                self._enqueue_point_cloud(info, converted, frame)
                points = converted
        return points

    def drain_point_clouds(self, device_id: str) -> list[dict]:
        """Consume FIFO once. UDP times are first-segment host reads, not device time.

        LMD times are assembled-telegram receipt before parsing; timestamp_source
        distinguishes these from UDP socket reads and legacy API-return timing.

        Monotonic receipts never decrease, including across drains. Late
        completions are rejected with late_complete_frames_dropped incremented.
        Wall time is the unmodified host clock and can jump with clock correction.
        device_frame is the scanner counter (None if unavailable), whereas frame
        is a local queue sequence; device_frame_gaps exposes wholly absent scans.
        At capacity the oldest frame is evicted and queue_overflows increments.
        """
        info = self.receivers.get(device_id)
        if not isinstance(info, dict):
            return []
        with info["lock"]:
            queue = info.setdefault("capture_queue", deque())
            frames = list(queue)
            queue.clear()
            return frames

    def _summarize_segment_payload(self, segment_list: list) -> dict:
        """Lightweight diagnostics about the latest raw segment payload."""
        stats = {
            "segments_in_batch": int(len(segment_list or [])),
            "modules": 0,
            "scans": 0,
            "echoes": None,
            "beams": None,
            "has_distance": False,
            "distance_samples": 0,
            "distance_nonzero": 0,
            "distance_zero": 0,
            "keys": [],
            "timestamp": time.time(),
        }
        if not segment_list:
            return stats
        seg = segment_list[0]
        if isinstance(seg, dict):
            stats["keys"] = sorted(list(seg.keys()))
        modules = seg.get("Modules") if isinstance(seg, dict) else None
        if isinstance(modules, list) and modules:
            stats["modules"] = len(modules)
            first_mod = modules[0] or {}
            scans = first_mod.get("SegmentData", [])
        else:
            scans = seg.get("SegmentData", []) if isinstance(seg, dict) else []
        if isinstance(scans, list) and scans:
            stats["scans"] = len(scans)
            first_scan = scans[0] or {}
            distances = first_scan.get("Distance", [])
            if isinstance(distances, list) and distances:
                stats["has_distance"] = True
                stats["echoes"] = len(distances)
                try:
                    stats["beams"] = len(distances[0]) if distances[0] is not None else 0
                except Exception:
                    stats["beams"] = None
                try:
                    nonzero = 0
                    zero = 0
                    samples = 0
                    for echo in distances:
                        if not isinstance(echo, (list, tuple, np.ndarray)):
                            continue
                        for d in echo:
                            samples += 1
                            if d and d > 0:
                                nonzero += 1
                            else:
                                zero += 1
                    stats["distance_samples"] = samples
                    stats["distance_nonzero"] = nonzero
                    stats["distance_zero"] = zero
                except Exception:
                    pass
        return stats

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
                frame = int(info.get("frame_counter", 0))
                ts = info.get("latest_update_ts")
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
            if 'process_health' in info:
                remote = dict(info['process_health'])
                remote['stream_epoch'] = info['receiver'].stream_epoch
                age = remote.get('latest_data_age_s')
                if age is not None:
                    remote['latest_data_age_s'] = age + max(0, now - info['process_health_received'])
                    if remote['latest_data_age_s'] > 2.5:
                        remote['data_rate_hz'] = 0.0
                remote['queue_overflows'] = remote.get('queue_overflows', 0) + info.get('queue_overflows', 0)
                remote['connected'] = bool(info['receiver'].connected)
                if not remote['connected']:
                    remote.update(availability='offline', data_rate_hz=0.0, last_error=info.get('last_error'))
                snapshot[device_id] = remote
                continue
            configured_segments = getattr(device_cfg, "segments_per_scan", None) if device_cfg else None
            data_ts = info.get("data_timestamps")
            data_hz = None
            if isinstance(data_ts, deque) and len(data_ts) >= 2:
                dt = float(data_ts[-1] - data_ts[0])
                if dt > 0:
                    data_hz = float((len(data_ts) - 1) / dt)
            latest_ts = info.get("latest_update_ts")
            age_s = (now - latest_ts) if latest_ts else None
            if age_s is not None and age_s > 2.5:
                data_hz = 0.0
            transport = getattr(info.get("receiver"), "transport_layer", None)
            ingress = transport.get_metrics() if hasattr(transport, "get_metrics") else {}
            snapshot[device_id] = {
                "ingress": ingress,
                "ingress_queue_dropped": ingress.get("ingress_queue_dropped", 0),
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
                "segments_per_scan_configured": configured_segments,
                "expected_segment_pattern": info.get("expected_segment_pattern") or [],
                "dominant_segment_pattern": info.get("dominant_segment_pattern") or [],
                "dominant_pattern_samples": int(info.get("dominant_pattern_samples", 0) or 0),
                "pattern_switch_count": int(info.get("pattern_switch_count", 0) or 0),
                "pattern_last_switch_ts": info.get("pattern_last_switch_ts"),
                "last_closed_frame_number": info.get("last_closed_frame_number"),
                "last_closed_segment_pattern": info.get("last_closed_segment_pattern") or [],
                "last_complete_frame_number": info.get("last_complete_frame_number"),
                "last_complete_segment_pattern": info.get("last_complete_segment_pattern") or [],
                "incomplete_frames_dropped": int(info.get("incomplete_frames_dropped", 0) or 0),
                "queue_overflows": int(info.get("queue_overflows", 0) or 0),
                "duplicate_segments": int(info.get("duplicate_segments", 0) or 0),
                "late_complete_frames_dropped": info.get("late_complete_frames_dropped", 0),
                "device_frame_gaps": info.get("device_frame_gaps", 0),
                "device_frame_regressions": info.get("device_frame_regressions", 0),
                "device_frame_tracking_supported": info.get("device_frame_tracking_supported", False),
                "pattern_locked": info.get("pattern_locked", False),
                "pattern_learning_frames_dropped": info.get("pattern_learning_frames_dropped", 0),
                "last_partial_frame_number": info.get("last_partial_frame_number"),
                "last_partial_segment_counters": info.get("last_partial_segment_counters") or [],
                "pipeline_timing_ms": {
                    "udp_receive": self._summarize_timing(info.get("perf_udp_receive_ms")),
                    "assemble": self._summarize_timing(info.get("perf_assemble_ms")),
                    "convert": self._summarize_timing(info.get("perf_convert_ms")),
                    "store": self._summarize_timing(info.get("perf_store_ms")),
                    "cycle": self._summarize_timing(info.get("perf_cycle_ms")),
                },
                "last_points_count": int(info.get("perf_last_points_count", 0) or 0),
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
                        segment_pattern=getattr(d, "segment_pattern", None),
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
