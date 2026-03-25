#!/usr/bin/env python
"""Diagnostyczny viewer chmury punktow z picoScana."""

from __future__ import annotations

import argparse
import json
import queue
import socket
import threading
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation

from scansegmentapi import compact as compact_api
from scansegmentapi.udp_handler import UDPHandler

UDP_RCVBUF_BYTES = 4 * 1024 * 1024


def _segment_points(segment: dict[str, Any]) -> np.ndarray:
    points: list[list[float]] = []
    for module in segment.get("Modules", []):
        phi_values = module.get("Phi", [0.0]) or [0.0]
        theta_start_values = module.get("ThetaStart", [0.0]) or [0.0]
        theta_stop_values = module.get("ThetaStop", [0.0]) or [0.0]
        for scan_idx, scan in enumerate(module.get("SegmentData", [])):
            distances = scan.get("Distance", [])
            if not distances:
                continue
            num_echos = len(distances)
            num_beams = len(distances[0]) if num_echos > 0 else 0
            if num_beams <= 0:
                continue
            phi = float(phi_values[scan_idx] if scan_idx < len(phi_values) else 0.0)
            theta_start = float(theta_start_values[scan_idx] if scan_idx < len(theta_start_values) else 0.0)
            theta_stop = float(theta_stop_values[scan_idx] if scan_idx < len(theta_stop_values) else theta_start)
            channel_theta = scan.get("ChannelTheta")
            rssi = scan.get("Rssi")
            for beam_idx in range(num_beams):
                if channel_theta is not None and len(channel_theta) > beam_idx:
                    theta = float(channel_theta[beam_idx])
                else:
                    denom = (num_beams - 1) if num_beams > 1 else 1
                    theta = theta_start + beam_idx * (theta_stop - theta_start) / denom
                for echo_idx in range(num_echos):
                    distance = float(distances[echo_idx][beam_idx])
                    if distance <= 0:
                        continue
                    x = distance * np.cos(theta) * np.cos(phi)
                    y = distance * np.cos(theta) * np.sin(phi)
                    z = distance * np.sin(theta)
                    intensity = 0.0
                    if rssi is not None and len(rssi) > echo_idx and len(rssi[echo_idx]) > beam_idx:
                        intensity = float(rssi[echo_idx][beam_idx])
                    points.append([x, y, z, intensity])
    if not points:
        return np.empty((0, 4), dtype=np.float32)
    return np.asarray(points, dtype=np.float32)


def _frame_to_points(frame_segments: list[dict[str, Any]]) -> np.ndarray:
    clouds = [_segment_points(segment) for segment in frame_segments]
    clouds = [cloud for cloud in clouds if cloud.size > 0]
    if not clouds:
        return np.empty((0, 4), dtype=np.float32)
    return np.vstack(clouds)


def _downsample(points: np.ndarray, limit: int) -> np.ndarray:
    if points.shape[0] <= limit:
        return points
    step = max(1, int(np.ceil(points.shape[0] / limit)))
    return points[::step]


@dataclass
class FramePacket:
    frame_number: int
    segment_counters: list[int]
    points: np.ndarray
    packet_rate_hz: float
    frame_rate_hz: float
    beams_per_segment: int
    echoes: int
    packet_len: int
    received_ts: float
    complete: bool
    missing_counters: list[int]
    expected_pattern: list[int]


class LiveCompactReceiver:
    def __init__(
        self,
        listen_ip: str,
        port: int,
        expected_sender: str,
        timeout_s: float,
        expected_segments: int,
        only_complete: bool,
        auto_recalc: bool,
        recalc_window: int,
        recalc_stable_frames: int,
        out_queue: queue.Queue,
    ):
        self.listen_ip = listen_ip
        self.port = int(port)
        self.expected_sender = expected_sender
        self.timeout_s = float(timeout_s)
        self.expected_segments = max(1, int(expected_segments))
        self.only_complete = bool(only_complete)
        self.auto_recalc = bool(auto_recalc)
        self.recalc_window = max(3, int(recalc_window))
        self.recalc_stable_frames = max(2, int(recalc_stable_frames))
        self.out_queue = out_queue
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.pending: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
        self.packet_times: list[float] = []
        self.frame_times: list[float] = []
        self.frames_emitted = 0
        self.frames_dropped_incomplete = 0
        self.last_emitted_frame_number: int | None = None
        self.last_dropped_frame_number: int | None = None
        self.expected_pattern: list[int] = list(range(self.expected_segments))
        self.pattern_history: deque[tuple[int, ...]] = deque(maxlen=self.recalc_window)
        self.pattern_switches = 0
        self.last_pattern_switch_ts: float | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def _run(self) -> None:
        transport = UDPHandler(self.listen_ip, self.port, 65535)
        try:
            transport.client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RCVBUF_BYTES)
        except Exception:
            pass
        transport.client.settimeout(self.timeout_s)
        try:
            while not self._stop.is_set():
                data, sender = transport.receive_new_scan_segment()
                sender_ip = sender[0] if sender else ""
                if not data:
                    continue
                if self.expected_sender and sender_ip != self.expected_sender:
                    continue
                payload = compact_api._verify_and_extract_payload(data)
                if payload is None:
                    continue
                segment = compact_api.parse_payload(payload)
                if segment is None:
                    continue
                module = (segment.get("Modules") or [{}])[0]
                frame_number = module.get("FrameNumber")
                segment_counter = module.get("SegmentCounter")
                if frame_number is None or segment_counter is None:
                    continue
                frame_number = int(frame_number)
                segment_counter = int(segment_counter)
                ts = time.time()
                self.packet_times.append(ts)
                self.packet_times = self.packet_times[-60:]
                self.pending.setdefault(frame_number, []).append(
                    {
                        "segment_counter": segment_counter,
                        "packet_len": len(data),
                        "segment": segment,
                        "timestamp": ts,
                    }
                )
                older = [key for key in self.pending.keys() if key < frame_number]
                for old_frame in older:
                    self._emit_frame(old_frame)
        finally:
            transport.client.close()

    def _queue_frame(self, frame_packet: FramePacket) -> None:
        try:
            self.out_queue.put_nowait(frame_packet)
        except queue.Full:
            try:
                self.out_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.out_queue.put_nowait(frame_packet)
            except queue.Full:
                pass

    def _emit_frame(self, frame_number: int) -> None:
        parts = self.pending.pop(frame_number, [])
        if not parts:
            return
        parts.sort(key=lambda item: item["segment_counter"])
        segment_counters = [int(item["segment_counter"]) for item in parts]
        observed_pattern = tuple(segment_counters)
        self.pattern_history.append(observed_pattern)
        if self.auto_recalc:
            self._maybe_recalculate_pattern()
        expected = list(self.expected_pattern)
        complete = segment_counters == expected
        missing_counters = [idx for idx in expected if idx not in segment_counters]
        if self.only_complete and not complete:
            self.frames_dropped_incomplete += 1
            self.last_dropped_frame_number = frame_number
            return
        frame_segments = [item["segment"] for item in parts]
        points = _frame_to_points(frame_segments)
        if points.shape[0] == 0:
            return
        first_module = (frame_segments[0].get("Modules") or [{}])[0]
        first_scan = ((first_module.get("SegmentData") or [{}])[0]) if first_module.get("SegmentData") else {}
        beams = len(first_scan.get("Distance", [[0]])[0]) if first_scan.get("Distance") else 0
        echoes = len(first_scan.get("Distance", [])) if first_scan.get("Distance") else 0
        now = time.time()
        self.frame_times.append(now)
        self.frame_times = self.frame_times[-30:]
        packet_rate_hz = 0.0
        if len(self.packet_times) >= 2:
            dt = self.packet_times[-1] - self.packet_times[0]
            if dt > 0:
                packet_rate_hz = (len(self.packet_times) - 1) / dt
        frame_rate_hz = 0.0
        if len(self.frame_times) >= 2:
            dt = self.frame_times[-1] - self.frame_times[0]
            if dt > 0:
                frame_rate_hz = (len(self.frame_times) - 1) / dt
        self.frames_emitted += 1
        self.last_emitted_frame_number = frame_number
        self._queue_frame(
            FramePacket(
                frame_number=frame_number,
                segment_counters=segment_counters,
                points=points,
                packet_rate_hz=packet_rate_hz,
                frame_rate_hz=frame_rate_hz,
                beams_per_segment=beams,
                echoes=echoes,
                packet_len=int(parts[0]["packet_len"]),
                received_ts=now,
                complete=complete,
                missing_counters=missing_counters,
                expected_pattern=list(self.expected_pattern),
            )
        )

    def _maybe_recalculate_pattern(self) -> None:
        if len(self.pattern_history) < self.recalc_stable_frames:
            return
        hist = Counter(self.pattern_history)
        dominant_pattern, dominant_count = hist.most_common(1)[0]
        if dominant_count < self.recalc_stable_frames:
            return
        dominant_pattern_list = list(dominant_pattern)
        if dominant_pattern_list == self.expected_pattern:
            return
        self.expected_pattern = dominant_pattern_list
        self.expected_segments = len(dominant_pattern_list)
        self.pattern_switches += 1
        self.last_pattern_switch_ts = time.time()


def _load_file_frame(path: Path) -> FramePacket:
    segments = json.loads(path.read_text(encoding="utf-8"))
    points = _frame_to_points(segments)
    module = (segments[0].get("Modules") or [{}])[0]
    first_scan = ((module.get("SegmentData") or [{}])[0]) if module.get("SegmentData") else {}
    segment_counters = [int((seg.get("Modules") or [{}])[0].get("SegmentCounter", -999)) for seg in segments]
    expected_segments = max(1, len(segment_counters))
    expected = list(range(expected_segments))
    return FramePacket(
        frame_number=int(module.get("FrameNumber", -1)),
        segment_counters=segment_counters,
        points=points,
        packet_rate_hz=0.0,
        frame_rate_hz=0.0,
        beams_per_segment=len(first_scan.get("Distance", [[0]])[0]) if first_scan.get("Distance") else 0,
        echoes=len(first_scan.get("Distance", [])) if first_scan.get("Distance") else 0,
        packet_len=0,
        received_ts=time.time(),
        complete=segment_counters == expected,
        missing_counters=[idx for idx in expected if idx not in segment_counters],
        expected_pattern=expected,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live viewer diagnostyczny dla picoScan compact UDP.")
    parser.add_argument("--mode", choices=["live", "file"], default="live")
    parser.add_argument("--listen-ip", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=2116)
    parser.add_argument("--expected-sender", default="192.168.0.10")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--expected-segments", type=int, default=5)
    parser.add_argument("--show-incomplete", action="store_true")
    parser.add_argument("--disable-auto-recalc", action="store_true")
    parser.add_argument("--recalc-window", type=int, default=12)
    parser.add_argument("--recalc-stable-frames", type=int, default=6)
    parser.add_argument(
        "--input-file",
        default=str(Path(__file__).resolve().parent / "output" / "latest_complete_frame.json"),
    )
    parser.add_argument("--max-points", type=int, default=18000)
    parser.add_argument("--point-size", type=float, default=2.0)
    return parser.parse_args()


def run_viewer(args: argparse.Namespace) -> int:
    frame_queue: queue.Queue[FramePacket] = queue.Queue(maxsize=3)
    receiver: LiveCompactReceiver | None = None
    current_frame: FramePacket | None = None

    if args.mode == "live":
        receiver = LiveCompactReceiver(
            listen_ip=args.listen_ip,
            port=args.port,
            expected_sender=args.expected_sender,
            timeout_s=args.timeout,
            expected_segments=args.expected_segments,
            only_complete=not args.show_incomplete,
            auto_recalc=not args.disable_auto_recalc,
            recalc_window=args.recalc_window,
            recalc_stable_frames=args.recalc_stable_frames,
            out_queue=frame_queue,
        )
        receiver.start()
    else:
        current_frame = _load_file_frame(Path(args.input_file))

    fig, (ax_xy, ax_xz) = plt.subplots(1, 2, figsize=(14, 7))
    fig.suptitle("picoScan Compact Viewer", fontsize=14)
    status_text = fig.text(0.02, 0.02, "", family="monospace", fontsize=10)

    def _style_axis(axis: Any, xlabel: str, ylabel: str) -> None:
        axis.set_facecolor("#f8fafc")
        axis.grid(True, alpha=0.25)
        axis.set_xlabel(xlabel)
        axis.set_ylabel(ylabel)
        axis.set_aspect("equal", adjustable="box")

    _style_axis(ax_xy, "X [mm]", "Y [mm]")
    _style_axis(ax_xz, "X [mm]", "Z [mm]")

    def update(_frame_index: int):
        nonlocal current_frame
        try:
            while True:
                current_frame = frame_queue.get_nowait()
        except queue.Empty:
            pass

        if current_frame is None:
            if receiver is not None:
                status_text.set_text(
                    "\n".join(
                        [
                            "Czekam na pelna ramke...",
                            f"expected_pattern={receiver.expected_pattern}",
                            f"pokazane_pelne={receiver.frames_emitted}",
                            f"odrzucone_niepelne={receiver.frames_dropped_incomplete}",
                            f"auto_recalc={receiver.auto_recalc} switch_count={receiver.pattern_switches}",
                        ]
                    )
                )
            else:
                status_text.set_text("Czekam na pelna ramke...")
            return []

        points = _downsample(current_frame.points, args.max_points)
        if points.shape[0] == 0:
            status_text.set_text("Odebrano ramke bez punktow.")
            return []

        intensity = points[:, 3]
        color_values = intensity if np.any(intensity > 0) else points[:, 2]

        ax_xy.cla()
        ax_xz.cla()
        _style_axis(ax_xy, "X [mm]", "Y [mm]")
        _style_axis(ax_xz, "X [mm]", "Z [mm]")
        ax_xy.scatter(points[:, 0], points[:, 1], c=color_values, s=args.point_size, cmap="viridis", linewidths=0)
        ax_xz.scatter(points[:, 0], points[:, 2], c=color_values, s=args.point_size, cmap="viridis", linewidths=0)
        ax_xy.set_title("Rzut XY")
        ax_xz.set_title("Rzut XZ")

        mins = points[:, :3].min(axis=0)
        maxs = points[:, :3].max(axis=0)
        status_text.set_text(
            "\n".join(
                [
                    f"frame={current_frame.frame_number}",
                    f"segmenty={current_frame.segment_counters}  count={len(current_frame.segment_counters)}  complete={current_frame.complete}",
                    f"expected_pattern={current_frame.expected_pattern}",
                    f"missing={current_frame.missing_counters if current_frame.missing_counters else '[]'}",
                    f"punkty={current_frame.points.shape[0]}  pokazane={points.shape[0]}",
                    f"beamy/segment={current_frame.beams_per_segment}  echa={current_frame.echoes}",
                    f"udp_pkt_rate={current_frame.packet_rate_hz:.1f} Hz  frame_rate={current_frame.frame_rate_hz:.1f} Hz",
                    (
                        f"pokazane_pelne={receiver.frames_emitted}  odrzucone_niepelne={receiver.frames_dropped_incomplete}  switch_count={receiver.pattern_switches}"
                        if receiver is not None
                        else "tryb_plikowy=true"
                    ),
                    f"x=[{mins[0]:.1f},{maxs[0]:.1f}]  y=[{mins[1]:.1f},{maxs[1]:.1f}]  z=[{mins[2]:.1f},{maxs[2]:.1f}]",
                ]
            )
        )
        return []

    interval_ms = 120 if args.mode == "live" else 1000
    animation = FuncAnimation(fig, update, interval=interval_ms, cache_frame_data=False)
    try:
        plt.show()
    finally:
        if receiver is not None:
            receiver.stop()
        del animation
    return 0


if __name__ == "__main__":
    raise SystemExit(run_viewer(parse_args()))
