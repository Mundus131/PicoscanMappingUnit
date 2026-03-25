#!/usr/bin/env python
"""Dluzsza diagnostyka zero-loss dla picoScan compact UDP."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import zlib
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

from scansegmentapi import compact as compact_api


DEFAULT_RCVBUF = 4 * 1024 * 1024


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dluzszy przechwyt ramek compact UDP z analiza utraty segmentow.")
    parser.add_argument("--listen-ip", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=2116)
    parser.add_argument("--expected-sender", default="192.168.0.10")
    parser.add_argument("--duration", type=float, default=20.0, help="Czas akwizycji w sekundach.")
    parser.add_argument("--max-frames", type=int, default=400, help="Maksymalna liczba zamknietych ramek.")
    parser.add_argument("--socket-timeout", type=float, default=0.5)
    parser.add_argument("--rcvbuf", type=int, default=DEFAULT_RCVBUF)
    parser.add_argument("--report-every", type=float, default=2.0, help="Co ile sekund wypisywac status.")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "output"),
    )
    return parser.parse_args()


def _extract_payload(data: bytes) -> bytes | None:
    if len(data) < 8:
        return None
    if data[0:4] != b"\x02\x02\x02\x02":
        return None
    expected_crc = int.from_bytes(data[-4:], "little")
    payload = data[:-4]
    if zlib.crc32(payload) != expected_crc:
        return None
    return payload


def _segment_summary(segment: dict[str, Any], sender_ip: str, packet_len: int, ts: float) -> dict[str, Any]:
    module = (segment.get("Modules") or [{}])[0]
    scans = module.get("SegmentData") or []
    first_scan = scans[0] if scans else {}
    distance = first_scan.get("Distance") or []
    return {
        "frame_number": int(module.get("FrameNumber")),
        "segment_counter": int(module.get("SegmentCounter")),
        "telegram_counter": segment.get("TelegramCounter"),
        "sender_ip": sender_ip,
        "packet_len": int(packet_len),
        "beams": len(distance[0]) if distance and hasattr(distance[0], "__len__") else 0,
        "echoes": len(distance) if hasattr(distance, "__len__") else 0,
        "timestamp": ts,
    }


def _close_frame(frame_number: int, entries: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    counters = sorted(int(item["segment_counter"]) for item in entries)
    starts_at_zero = bool(counters) and counters[0] == 0
    contiguous = bool(counters) and counters == list(range(counters[0], counters[0] + len(counters)))
    return {
        "frame_number": int(frame_number),
        "segment_counters": counters,
        "segment_count": len(counters),
        "starts_at_zero": starts_at_zero,
        "contiguous": contiguous,
        "reason_closed": reason,
        "first_ts": entries[0]["timestamp"] if entries else None,
        "last_ts": entries[-1]["timestamp"] if entries else None,
        "packet_len": entries[0]["packet_len"] if entries else None,
        "beams": entries[0]["beams"] if entries else None,
        "echoes": entries[0]["echoes"] if entries else None,
    }


def _print_progress(
    closed_frames: list[dict[str, Any]],
    decoded_segments: int,
    crc_failures: int,
    parse_failures: int,
    start_ts: float,
) -> None:
    elapsed = max(0.001, time.time() - start_ts)
    print(
        f"[{elapsed:6.2f}s] segments={decoded_segments} closed_frames={len(closed_frames)} "
        f"seg_rate={decoded_segments/elapsed:6.1f}/s crc_fail={crc_failures} parse_fail={parse_failures}"
    )


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(args.rcvbuf))
    sock.bind((args.listen_ip, int(args.port)))
    sock.settimeout(float(args.socket_timeout))

    pending: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
    closed_frames: list[dict[str, Any]] = []
    decoded_segments = 0
    ignored_packets = 0
    timeouts = 0
    crc_failures = 0
    parse_failures = 0
    start_ts = time.time()
    last_report_ts = start_ts

    try:
        while (time.time() - start_ts) < args.duration and len(closed_frames) < args.max_frames:
            now = time.time()
            if now - last_report_ts >= args.report_every:
                _print_progress(closed_frames, decoded_segments, crc_failures, parse_failures, start_ts)
                last_report_ts = now

            try:
                data, sender = sock.recvfrom(65535)
            except socket.timeout:
                timeouts += 1
                continue

            sender_ip = sender[0] if sender else ""
            if args.expected_sender and sender_ip != args.expected_sender:
                ignored_packets += 1
                continue

            payload = _extract_payload(data)
            if payload is None:
                crc_failures += 1
                continue

            segment = compact_api.parse_payload(payload)
            if segment is None:
                parse_failures += 1
                continue

            ts = time.time()
            try:
                summary = _segment_summary(segment, sender_ip, len(data), ts)
            except Exception:
                parse_failures += 1
                continue

            frame_number = int(summary["frame_number"])
            pending.setdefault(frame_number, []).append(summary)
            decoded_segments += 1

            older_frames = [f for f in pending.keys() if f < frame_number]
            for old_frame in older_frames:
                closed_frames.append(_close_frame(old_frame, pending.pop(old_frame), "next_frame_seen"))

        for frame_number, entries in list(pending.items()):
            closed_frames.append(_close_frame(frame_number, entries, "capture_finished"))

        contiguous_from_zero = [
            frame for frame in closed_frames if frame["starts_at_zero"] and frame["contiguous"] and frame["reason_closed"] != "capture_finished"
        ]
        likely_segments = None
        if contiguous_from_zero:
            count_hist = Counter(int(frame["segment_count"]) for frame in contiguous_from_zero)
            likely_segments = count_hist.most_common(1)[0][0]

        missing_frame_numbers: list[int] = []
        frame_numbers = [int(frame["frame_number"]) for frame in closed_frames]
        if len(frame_numbers) >= 2:
            for prev, cur in zip(frame_numbers, frame_numbers[1:]):
                if cur - prev > 1:
                    missing_frame_numbers.extend(list(range(prev + 1, cur)))

        incomplete_frames = []
        for frame in closed_frames:
            counters = frame["segment_counters"]
            missing_counters: list[int] = []
            if likely_segments is not None:
                expected = list(range(likely_segments))
                missing_counters = [idx for idx in expected if idx not in counters]
                frame["complete"] = counters == expected
            else:
                frame["complete"] = bool(frame["starts_at_zero"] and frame["contiguous"])
            frame["missing_counters"] = missing_counters
            if not frame["complete"] and frame["reason_closed"] != "capture_finished":
                incomplete_frames.append(frame)

        summary = {
            "sensor_ip": args.expected_sender,
            "listen_ip": args.listen_ip,
            "listen_port": args.port,
            "capture_started_ts": start_ts,
            "capture_duration_s": round(time.time() - start_ts, 3),
            "decoded_segments": decoded_segments,
            "ignored_packets": ignored_packets,
            "timeouts": timeouts,
            "crc_failures": crc_failures,
            "parse_failures": parse_failures,
            "closed_frames": len(closed_frames),
            "likely_segments_per_frame": likely_segments,
            "missing_frame_numbers": missing_frame_numbers,
            "incomplete_frame_count": len(incomplete_frames),
            "complete_frame_count": sum(1 for frame in closed_frames if frame.get("complete")),
            "zero_loss_pass": len(incomplete_frames) == 0 and len(missing_frame_numbers) == 0 and decoded_segments > 0,
            "incomplete_examples": incomplete_frames[:20],
            "frames": closed_frames,
        }
        output_path = output_dir / "continuous_capture_report.json"
        output_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
        print(json.dumps(summary, indent=2, default=_json_default))
        print(f"\nRaport zapisany do: {output_path}")
        return 0 if summary["zero_loss_pass"] else 3
    finally:
        sock.close()


if __name__ == "__main__":
    sys.exit(main())
