#!/usr/bin/env python3
"""Estimate cluster TFLOPS from exo benchmark throughput.

This uses a rough inference-time rule of thumb:

    TFLOPS ≈ generation_tps * 2 * active_params / 1e12

For MoE models, `active_params` should be the active parameter count, not the
total parameter count. The estimate ignores attention overhead and is meant for
practical cluster comparison rather than exact FLOP accounting.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def estimate_tflops(generation_tps: float, active_params_billion: float) -> float:
    active_params = active_params_billion * 1_000_000_000
    return generation_tps * (2.0 * active_params) / 1_000_000_000_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generation-tps",
        type=float,
        help="Measured generation_tps from exo bench output.",
    )
    parser.add_argument(
        "--active-params-b",
        type=float,
        required=True,
        help="Active parameter count in billions, e.g. 23 for MiniMax M3.",
    )
    parser.add_argument(
        "--bench-json",
        type=Path,
        help="Path to an exo bench JSON file. Uses the first run's generation_tps.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    generation_tps = args.generation_tps
    if generation_tps is None:
        if args.bench_json is None:
            print("Provide --generation-tps or --bench-json", file=sys.stderr)
            return 1
        payload = json.loads(args.bench_json.read_text())
        runs = payload.get("runs") or []
        if not runs:
            print("No runs found in bench JSON", file=sys.stderr)
            return 1
        stats = runs[0].get("stats") or {}
        generation_tps = float(stats.get("generation_tps") or 0.0)

    tflops = estimate_tflops(generation_tps, args.active_params_b)
    print(f"generation_tps={generation_tps:.2f}")
    print(f"active_params_b={args.active_params_b:.2f}")
    print(f"estimated_cluster_tflops={tflops:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

