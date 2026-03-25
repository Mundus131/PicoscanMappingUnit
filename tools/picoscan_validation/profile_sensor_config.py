#!/usr/bin/env python
"""Profiluje biezaca konfiguracje picoScana na podstawie rzeczywistego streamu UDP."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profil aktualnej konfiguracji picoScana z pomiaru UDP.")
    parser.add_argument("--label", default="current", help="Etykieta profilu, np. freq25_filterOff")
    parser.add_argument("--duration", type=float, default=20.0, help="Czas przechwytu w sekundach")
    parser.add_argument("--max-frames", type=int, default=400, help="Limit zamknietych ramek")
    parser.add_argument("--listen-ip", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=2116)
    parser.add_argument("--expected-sender", default="192.168.0.10")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "output"),
    )
    return parser.parse_args()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    capture_cmd = [
        str(base_dir / ".venv" / "Scripts" / "python.exe"),
        str(base_dir / "continuous_capture.py"),
        "--duration",
        str(args.duration),
        "--max-frames",
        str(args.max_frames),
        "--listen-ip",
        args.listen_ip,
        "--port",
        str(args.port),
        "--expected-sender",
        args.expected_sender,
        "--output-dir",
        str(output_dir),
        "--report-every",
        "2",
    ]

    started_at = time.time()
    result = subprocess.run(capture_cmd, cwd=base_dir.parent.parent, capture_output=True, text=True)
    report_path = output_dir / "continuous_capture_report.json"
    if not report_path.exists():
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        print("Nie znaleziono continuous_capture_report.json", file=sys.stderr)
        return 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    frames = report.get("frames") or []
    complete_frames = [frame for frame in frames if frame.get("complete")]
    effective_frames = complete_frames if complete_frames else [
        frame for frame in frames if frame.get("reason_closed") != "capture_finished"
    ]
    if not effective_frames:
        print(json.dumps(report, indent=2))
        print("Brak ramek do profilowania.", file=sys.stderr)
        return 2

    pattern_hist = Counter(tuple(frame.get("segment_counters") or []) for frame in effective_frames)
    dominant_pattern, dominant_pattern_count = pattern_hist.most_common(1)[0]
    dominant_pattern = list(dominant_pattern)

    first_ts = effective_frames[0].get("first_ts")
    last_ts = effective_frames[-1].get("last_ts")
    frame_rate_hz = None
    if len(effective_frames) >= 2 and first_ts is not None and last_ts is not None and float(last_ts) > float(first_ts):
        frame_rate_hz = (len(effective_frames) - 1) / (float(last_ts) - float(first_ts))

    segment_rate_hz = None
    if report.get("capture_duration_s"):
        segment_rate_hz = float(report.get("decoded_segments", 0)) / float(report["capture_duration_s"])

    profile = {
        "label": args.label,
        "created_ts": started_at,
        "sensor_ip": args.expected_sender,
        "listen_ip": args.listen_ip,
        "listen_port": args.port,
        "capture_duration_s": report.get("capture_duration_s"),
        "zero_loss_pass": report.get("zero_loss_pass"),
        "likely_segments_per_frame": report.get("likely_segments_per_frame"),
        "complete_frame_count": report.get("complete_frame_count"),
        "incomplete_frame_count": report.get("incomplete_frame_count"),
        "missing_frame_numbers": report.get("missing_frame_numbers"),
        "dominant_segment_pattern": dominant_pattern,
        "dominant_segment_pattern_count": dominant_pattern_count,
        "dominant_segment_pattern_starts_at_zero": bool(dominant_pattern and dominant_pattern[0] == 0),
        "frame_rate_hz_estimated": frame_rate_hz,
        "segment_rate_hz_estimated": segment_rate_hz,
        "packet_len_bytes": effective_frames[0].get("packet_len"),
        "beams_per_segment": effective_frames[0].get("beams"),
        "echoes": effective_frames[0].get("echoes"),
        "top_complete_pattern": complete_frames[0].get("segment_counters") if complete_frames else None,
        "source_report": report_path,
        "capture_stdout_tail": result.stdout.splitlines()[-20:],
        "capture_return_code": result.returncode,
    }

    profile_path = output_dir / f"profile_{args.label}.json"
    profile_path.write_text(json.dumps(profile, indent=2, default=_json_default), encoding="utf-8")

    print(json.dumps(profile, indent=2, default=_json_default))
    print(f"\nProfil zapisany do: {profile_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
