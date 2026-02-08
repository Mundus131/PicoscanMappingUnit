"""
Post-acquisition log measurement script.

Fetches the latest accumulated point cloud from the API and computes
log measurements by fitting circles over windows of profiles.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from urllib.error import URLError

from app.analysis.log_measurement import compute_log_metrics


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Log measurement after acquisition")
    parser.add_argument("--base-url", default="http://localhost:8000/api/v1", help="API base URL")
    parser.add_argument("--profiling-distance-mm", type=float, default=None, help="Override profiling distance (mm)")
    parser.add_argument("--window-profiles", type=int, default=10, help="Profiles per measurement window")
    parser.add_argument("--min-points", type=int, default=50, help="Minimum points per window")
    parser.add_argument("--output", default="analysis_results.json", help="Output JSON file")
    args = parser.parse_args()

    latest_url = f"{args.base_url}/acquisition/trigger/latest-cloud?max_points=0"
    try:
        latest = fetch_json(latest_url)
    except URLError as exc:
        raise SystemExit(f"Failed to fetch latest cloud: {exc}")

    points = latest.get("points") or []
    if not points:
        raise SystemExit("No points returned from latest-cloud. Stop acquisition and try again.")

    profiling_distance_mm = args.profiling_distance_mm
    if profiling_distance_mm is None:
        try:
            motion = fetch_json(f"{args.base_url}/calibration/motion-settings")
            profiling_distance_mm = motion.get("profiling_distance_mm", None)
        except Exception:
            profiling_distance_mm = None
    if profiling_distance_mm is None:
        profiling_distance_mm = 10.0

    result = compute_log_metrics(
        points,
        profiling_distance_mm=profiling_distance_mm,
        window_profiles=args.window_profiles,
        min_points=args.min_points,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"Saved results to {args.output}")
    print(f"Total slices: {result['total_slices']}, volume_m3: {result['volume_m3']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
