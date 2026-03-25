#!/usr/bin/env python
"""Interaktywny builder 3D z kolejnych profiliskanow picoScana."""

from __future__ import annotations

import argparse
import json
import queue
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button

from picoscan_viewer import FramePacket, LiveCompactReceiver


def _downsample(points: np.ndarray, limit: int) -> np.ndarray:
    if points.shape[0] <= limit:
        return points
    step = max(1, int(np.ceil(points.shape[0] / limit)))
    return points[::step]


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Buduje obraz 3D z kolejnych pelnych profiliskanow.")
    parser.add_argument("--listen-ip", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=2116)
    parser.add_argument("--expected-sender", default="192.168.0.10")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--expected-segments", type=int, default=5)
    parser.add_argument("--profiles", type=int, default=50, help="Ile kolejnych profiliskanow zebrac po kliknieciu Start.")
    parser.add_argument("--y-step-mm", type=float, default=1.0, help="Przesuniecie kolejnych profili w osi Y.")
    parser.add_argument("--max-points", type=int, default=30000)
    parser.add_argument("--point-size", type=float, default=2.0)
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "output"),
    )
    return parser.parse_args()


def run_app(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    frame_queue: queue.Queue[FramePacket] = queue.Queue(maxsize=8)
    receiver = LiveCompactReceiver(
        listen_ip=args.listen_ip,
        port=args.port,
        expected_sender=args.expected_sender,
        timeout_s=args.timeout,
        expected_segments=args.expected_segments,
        only_complete=True,
        auto_recalc=True,
        recalc_window=12,
        recalc_stable_frames=6,
        out_queue=frame_queue,
    )
    receiver.start()

    current_frame: FramePacket | None = None
    capture_active = False
    saved_profiles: list[dict[str, Any]] = []
    point_cloud_3d = np.empty((0, 4), dtype=np.float32)
    last_saved_path: Path | None = None

    fig = plt.figure(figsize=(15, 8))
    ax_live = fig.add_subplot(1, 2, 1)
    ax_3d = fig.add_subplot(1, 2, 2, projection="3d")
    fig.suptitle("picoScan Profile Stack Builder", fontsize=15)
    status_text = fig.text(0.02, 0.02, "", family="monospace", fontsize=10)

    start_ax = fig.add_axes([0.37, 0.9, 0.11, 0.05])
    reset_ax = fig.add_axes([0.50, 0.9, 0.11, 0.05])
    save_ax = fig.add_axes([0.63, 0.9, 0.11, 0.05])
    start_button = Button(start_ax, "Start")
    reset_button = Button(reset_ax, "Reset")
    save_button = Button(save_ax, "Save")

    def _style_axis(axis: Any, xlabel: str, ylabel: str) -> None:
        axis.set_facecolor("#f8fafc")
        axis.grid(True, alpha=0.25)
        axis.set_xlabel(xlabel)
        axis.set_ylabel(ylabel)
        axis.set_aspect("equal", adjustable="box")

    def _style_3d(axis: Any) -> None:
        axis.set_xlabel("X [mm]")
        axis.set_ylabel("Y [mm]")
        axis.set_zlabel("Z [mm]")
        axis.grid(True, alpha=0.25)
        axis.view_init(elev=20, azim=-60)

    def _rebuild_3d_cloud() -> np.ndarray:
        clouds = []
        for idx, profile in enumerate(saved_profiles):
            pts = np.asarray(profile["points"], dtype=np.float32)
            shifted = np.array(pts, copy=True)
            shifted[:, 1] = shifted[:, 1] + idx * float(args.y_step_mm)
            clouds.append(shifted)
        if not clouds:
            return np.empty((0, 4), dtype=np.float32)
        return np.vstack(clouds)

    def _on_start(_event: Any) -> None:
        nonlocal capture_active, saved_profiles, point_cloud_3d, last_saved_path
        capture_active = True
        saved_profiles = []
        point_cloud_3d = np.empty((0, 4), dtype=np.float32)
        last_saved_path = None

    def _on_reset(_event: Any) -> None:
        nonlocal capture_active, saved_profiles, point_cloud_3d, last_saved_path
        capture_active = False
        saved_profiles = []
        point_cloud_3d = np.empty((0, 4), dtype=np.float32)
        last_saved_path = None

    def _on_save(_event: Any) -> None:
        nonlocal last_saved_path
        if point_cloud_3d.shape[0] == 0:
            return
        payload = {
            "profiles_requested": int(args.profiles),
            "profiles_collected": len(saved_profiles),
            "y_step_mm": float(args.y_step_mm),
            "expected_pattern": current_frame.expected_pattern if current_frame is not None else None,
            "frames": [
                {
                    "frame_number": profile["frame_number"],
                    "segment_counters": profile["segment_counters"],
                    "point_count": int(np.asarray(profile["points"]).shape[0]),
                }
                for profile in saved_profiles
            ],
            "point_cloud": point_cloud_3d,
        }
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        last_saved_path = output_dir / f"profile_stack_{timestamp}.json"
        last_saved_path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")

    start_button.on_clicked(_on_start)
    reset_button.on_clicked(_on_reset)
    save_button.on_clicked(_on_save)

    def update(_frame_idx: int):
        nonlocal current_frame, capture_active, point_cloud_3d
        try:
            while True:
                current_frame = frame_queue.get_nowait()
                if capture_active and current_frame is not None:
                    saved_profiles.append(
                        {
                            "frame_number": current_frame.frame_number,
                            "segment_counters": list(current_frame.segment_counters),
                            "points": np.array(current_frame.points, copy=True),
                        }
                    )
                    point_cloud_3d = _rebuild_3d_cloud()
                    if len(saved_profiles) >= int(args.profiles):
                        capture_active = False
        except queue.Empty:
            pass

        ax_live.cla()
        ax_3d.cla()
        _style_axis(ax_live, "X [mm]", "Z [mm]")
        _style_3d(ax_3d)

        if current_frame is not None and current_frame.points.shape[0] > 0:
            live_points = _downsample(current_frame.points, min(args.max_points, 12000))
            colors = live_points[:, 3] if np.any(live_points[:, 3] > 0) else live_points[:, 2]
            ax_live.scatter(live_points[:, 0], live_points[:, 2], c=colors, s=args.point_size, cmap="viridis", linewidths=0)
            ax_live.set_title("Aktualny Profil XZ")

        if point_cloud_3d.shape[0] > 0:
            cloud = _downsample(point_cloud_3d, args.max_points)
            colors = cloud[:, 3] if np.any(cloud[:, 3] > 0) else cloud[:, 2]
            ax_3d.scatter(cloud[:, 0], cloud[:, 1], cloud[:, 2], c=colors, s=args.point_size, cmap="viridis", linewidths=0)
            ax_3d.set_title("Zlozony Obraz 3D")
        else:
            ax_3d.set_title("Zlozony Obraz 3D")

        current_segments = current_frame.segment_counters if current_frame is not None else []
        current_pattern = current_frame.expected_pattern if current_frame is not None else receiver.expected_pattern
        current_points = int(current_frame.points.shape[0]) if current_frame is not None else 0
        status_lines = [
            f"capture_active={capture_active}",
            f"profiles_collected={len(saved_profiles)}/{args.profiles}",
            f"y_step_mm={args.y_step_mm}",
            f"current_frame={current_frame.frame_number if current_frame is not None else '-'}",
            f"current_segments={current_segments}",
            f"expected_pattern={current_pattern}",
            f"current_points={current_points}",
            f"stack_points={int(point_cloud_3d.shape[0])}",
            f"receiver_frames={receiver.frames_emitted} dropped_incomplete={receiver.frames_dropped_incomplete}",
            f"pattern_switches={receiver.pattern_switches}",
            f"saved_file={last_saved_path if last_saved_path is not None else '-'}",
        ]
        status_text.set_text("\n".join(status_lines))
        return []

    animation = FuncAnimation(fig, update, interval=120, cache_frame_data=False)
    try:
        plt.show()
    finally:
        receiver.stop()
        del animation
    return 0


if __name__ == "__main__":
    raise SystemExit(run_app(parse_args()))
