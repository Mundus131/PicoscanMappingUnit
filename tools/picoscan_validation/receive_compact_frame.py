#!/usr/bin/env python
"""Minimalny probe UDP dla SICK picoScan w formacie compact."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from scansegmentapi import compact as compact_api
from scansegmentapi.udp_handler import UDPHandler


def _summarize_segment(segment: dict[str, Any], sender_ip: str, packet_len: int) -> dict[str, Any]:
    modules = segment.get("Modules") or []
    first_module = modules[0] if modules else {}
    scans = first_module.get("SegmentData") or []
    first_scan = scans[0] if scans else {}
    distances = first_scan.get("Distance") or []
    echoes = len(distances) if hasattr(distances, "__len__") else 0
    beams = len(distances[0]) if echoes and hasattr(distances[0], "__len__") else 0
    return {
        "sender_ip": sender_ip,
        "packet_len": int(packet_len),
        "frame_number": first_module.get("FrameNumber"),
        "segment_counter": first_module.get("SegmentCounter"),
        "telegram_counter": first_module.get("TelegramCounter"),
        "modules": len(modules),
        "scans_in_first_module": len(scans),
        "echoes_in_first_scan": echoes,
        "beams_in_first_scan": beams,
        "timestamp": time.time(),
    }


def _frame_report(frame_number: int, summaries: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    counters = sorted(
        int(item["segment_counter"])
        for item in summaries
        if item.get("segment_counter") is not None
    )
    single_segment = counters == [-1]
    starts_at_zero = bool(counters) and counters[0] == 0
    contiguous = single_segment or counters == list(range(counters[-1] + 1)) if counters else False
    complete_guess = single_segment or (starts_at_zero and contiguous and reason != "capture_finished")
    return {
        "frame_number": int(frame_number),
        "segment_count": len(summaries),
        "segment_counters": counters,
        "single_segment_frame": single_segment,
        "starts_at_zero": starts_at_zero,
        "contiguous": contiguous,
        "complete_guess": complete_guess,
        "reason_closed": reason,
        "segments": summaries,
    }


def _close_older_frames(
    pending: "OrderedDict[int, list[dict[str, Any]]]",
    completed: list[dict[str, Any]],
    current_frame: int,
) -> None:
    older = [frame_no for frame_no in pending.keys() if frame_no < current_frame]
    for frame_no in older:
        completed.append(_frame_report(frame_no, pending.pop(frame_no), "next_frame_seen"))


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Odbior pelnej ramki compact UDP z picoScana.")
    parser.add_argument("--listen-ip", default="0.0.0.0", help="Adres lokalny do bindowania UDP.")
    parser.add_argument("--port", type=int, default=2116, help="Port UDP lokalnego odbiornika.")
    parser.add_argument(
        "--expected-sender",
        default="192.168.0.10",
        help="Oczekiwany adres IP sensora. Inne pakiety beda ignorowane.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Timeout pojedynczego recvfrom w sekundach.",
    )
    parser.add_argument(
        "--capture-seconds",
        type=float,
        default=20.0,
        help="Maksymalny czas nasluchu.",
    )
    parser.add_argument(
        "--max-packets",
        type=int,
        default=100,
        help="Bezpieczny limit liczby pakietow do odebrania.",
    )
    parser.add_argument(
        "--observe-frames",
        type=int,
        default=3,
        help="Ile zamknietych ramek zebrac przed zakonczeniem.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "output"),
        help="Katalog na raporty JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    transport = UDPHandler(args.listen_ip, args.port, 65535)
    transport.client.settimeout(float(args.timeout))

    pending_frames: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
    raw_frame_segments: dict[int, list[dict[str, Any]]] = {}
    completed_frames: list[dict[str, Any]] = []
    ignored_packets = 0
    timeout_count = 0
    crc_failures = 0
    parse_failures = 0
    decoded_segments = 0
    first_packet_ts = None
    start_ts = time.time()

    try:
        while decoded_segments < args.max_packets and (time.time() - start_ts) < args.capture_seconds:
            data, sender = transport.receive_new_scan_segment()
            sender_ip = sender[0] if sender else ""

            if not data:
                timeout_count += 1
                continue

            if args.expected_sender and sender_ip != args.expected_sender:
                ignored_packets += 1
                continue

            if first_packet_ts is None:
                first_packet_ts = time.time()

            payload = compact_api._verify_and_extract_payload(data)
            if payload is None:
                crc_failures += 1
                continue

            segment = compact_api.parse_payload(payload)
            if segment is None:
                parse_failures += 1
                continue

            summary = _summarize_segment(segment, sender_ip, len(data))
            frame_number = summary["frame_number"]
            segment_counter = summary["segment_counter"]
            if frame_number is None or segment_counter is None:
                parse_failures += 1
                continue

            frame_number = int(frame_number)
            summary["frame_number"] = frame_number
            summary["segment_counter"] = int(segment_counter)
            pending_frames.setdefault(frame_number, []).append(summary)
            raw_frame_segments.setdefault(frame_number, []).append(segment)
            decoded_segments += 1

            if summary["segment_counter"] == -1:
                completed_frames.append(_frame_report(frame_number, pending_frames.pop(frame_number), "single_segment_marker"))
                raw_frame_segments[frame_number] = raw_frame_segments.get(frame_number, [])
            else:
                _close_older_frames(pending_frames, completed_frames, frame_number)

            if len(completed_frames) >= args.observe_frames:
                break

        for frame_number, summaries in list(pending_frames.items()):
            completed_frames.append(_frame_report(frame_number, summaries, "capture_finished"))

        complete_guesses = [frame for frame in completed_frames if frame["complete_guess"]]
        likely_segments = None
        if complete_guesses:
            counts: dict[int, int] = {}
            for frame in complete_guesses:
                count = int(frame["segment_count"])
                counts[count] = counts.get(count, 0) + 1
            likely_segments = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

        best_frame = None
        if complete_guesses:
            best_frame = max(complete_guesses, key=lambda frame: frame["segment_count"])
        elif completed_frames:
            best_frame = max(completed_frames, key=lambda frame: frame["segment_count"])

        complete_frame_path = None
        if best_frame is not None:
            frame_number = int(best_frame["frame_number"])
            complete_frame_path = output_dir / "latest_complete_frame.json"
            complete_frame_path.write_text(
                json.dumps(raw_frame_segments.get(frame_number, []), indent=2, default=_json_default),
                encoding="utf-8",
            )

        report = {
            "sensor_ip": args.expected_sender,
            "listen_ip": args.listen_ip,
            "listen_port": args.port,
            "capture_started_ts": start_ts,
            "capture_duration_s": round(time.time() - start_ts, 3),
            "first_packet_received": first_packet_ts is not None,
            "first_packet_delay_s": None if first_packet_ts is None else round(first_packet_ts - start_ts, 3),
            "decoded_segments": decoded_segments,
            "ignored_packets": ignored_packets,
            "timeouts": timeout_count,
            "crc_failures": crc_failures,
            "parse_failures": parse_failures,
            "closed_frames": len(completed_frames),
            "likely_segments_per_frame": likely_segments,
            "frames": completed_frames,
            "saved_complete_frame_path": complete_frame_path,
        }
        report_path = output_dir / "probe_report.json"
        report_path.write_text(json.dumps(report, indent=2, default=_json_default), encoding="utf-8")

        print(json.dumps(report, indent=2, default=_json_default))
        print(f"\nRaport zapisany do: {report_path}")
        if complete_frame_path:
            print(f"Najlepsza odebrana ramka zapisana do: {complete_frame_path}")
        return 0 if decoded_segments > 0 else 2
    finally:
        try:
            transport.client.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
