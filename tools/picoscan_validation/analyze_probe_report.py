#!/usr/bin/env python
"""Analizuje probe_report.json i podsumowuje kompletność ramek."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analiza kompletności ramek z probe_report.json")
    parser.add_argument(
        "--input",
        default=str(Path(__file__).resolve().parent / "output" / "probe_report.json"),
        help="Sciezka do probe_report.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    frames = report.get("frames") or []
    if not frames:
        print("Brak ramek w raporcie.")
        return 1

    patterns = Counter()
    complete = 0
    incomplete = 0
    exact_count = Counter()

    for frame in frames:
        counters = tuple(frame.get("segment_counters") or [])
        patterns[counters] += 1
        exact_count[len(counters)] += 1
        if frame.get("complete_guess"):
            complete += 1
        else:
            incomplete += 1

    print(f"closed_frames={len(frames)}")
    print(f"complete_frames={complete}")
    print(f"incomplete_frames={incomplete}")
    print(f"likely_segments_per_frame={report.get('likely_segments_per_frame')}")
    print("segment_count_histogram:")
    for count, freq in sorted(exact_count.items()):
        print(f"  {count}: {freq}")
    print("top_patterns:")
    for counters, freq in patterns.most_common(10):
        print(f"  {list(counters)} -> {freq}")

    recommended = report.get("likely_segments_per_frame")
    if recommended:
        print(f"recommendation: ustaw segments_per_scan={recommended}")
        print("recommendation: nie stosuj backoff po odebraniu niepelnej ramki")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
