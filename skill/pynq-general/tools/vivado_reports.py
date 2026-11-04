"""Extract common utilization/timing report rows into JSON and Markdown."""
from __future__ import annotations

import argparse
import json
import re
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import List, Optional


RESOURCE_ROWS = {
    "slice_luts": r"Slice LUTs\*?",
    "slice_registers": r"Slice Registers",
    "block_ram_tiles": r"Block RAM Tile",
    "dsps": r"DSPs",
}


def parse_utilization(text: str) -> dict:
    result = {}
    for key, label in RESOURCE_ROWS.items():
        pattern = re.compile(r"^\s*\|\s*" + label +
                             r"\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|\s*([\d.]+)\s*\|",
                             re.MULTILINE | re.IGNORECASE)
        match = pattern.search(text)
        if match:
            result[key] = {"used": int(match.group(1).replace(",", "")),
                           "available": int(match.group(2).replace(",", "")),
                           "utilization_percent": float(match.group(3))}
    if not result:
        raise ValueError("no recognized Vivado utilization rows found")
    return result


def parse_timing(text: str) -> dict:
    summary = re.compile(r"WNS\s*\(ns\)\s+TNS\s*\(ns\).*?\n\s*[-\s]+\n\s*([-\d.]+)\s+([-\d.]+)",
                         re.IGNORECASE | re.DOTALL)
    match = summary.search(text)
    if not match:
        # Some report versions place a table separator on the header line.
        summary = re.compile(r"WNS\s*\(ns\)\s+TNS\s*\(ns\).*?\n[^\n]*\n\s*([-\d.]+)\s+([-\d.]+)",
                             re.IGNORECASE | re.DOTALL)
        match = summary.search(text)
    if not match:
        raise ValueError("could not identify WNS/TNS summary rows")
    return {"wns_ns": float(match.group(1)), "tns_ns": float(match.group(2)),
            "timing_met_setup": float(match.group(1)) >= 0.0}


def summarize_performance(samples: list[dict]) -> dict:
    grouped = defaultdict(list)
    for sample in samples:
        stage = sample.get("stage")
        elapsed = sample.get("elapsed_ms")
        size = sample.get("bytes", 0)
        if not isinstance(stage, str) or not stage:
            raise ValueError("each performance sample needs a nonempty stage")
        if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed <= 0:
            raise ValueError("elapsed_ms must be a positive finite number")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("bytes must be a nonnegative integer")
        grouped[stage].append((float(elapsed), size))
    result = {}
    for stage, rows in grouped.items():
        times = sorted(value for value, _ in rows)
        p95_index = max(0, math.ceil(0.95 * len(times)) - 1)
        total_bytes = sum(size for _, size in rows)
        total_ms = sum(times)
        result[stage] = {"samples": len(times), "min_ms": times[0],
                         "median_ms": statistics.median(times), "p95_ms": times[p95_index],
                         "max_ms": times[-1], "mean_ms": statistics.mean(times),
                         "total_bytes": total_bytes,
                         "aggregate_mib_per_s": (total_bytes / (1024 * 1024) /
                                                  (total_ms / 1000.0) if total_bytes else None)}
    return result


def build_report(utilization_text: str, timing_text: str, metadata: dict,
                 performance_samples: Optional[List[dict]] = None) -> dict:
    resources = parse_utilization(utilization_text)
    timing = parse_timing(timing_text)
    return {"schema": "pynq-build-report/v1", "metadata": metadata,
            "resources": resources, "timing": timing,
            "performance": summarize_performance(performance_samples or [])}


def render_markdown(report: dict) -> str:
    metadata = report["metadata"]
    lines = ["# PYNQ Build and Validation Report", "",
             "- Project: %s" % metadata.get("project", "unspecified"),
             "- Board/part: %s" % metadata.get("board", "unspecified"),
             "- Tool version: %s" % metadata.get("tool_version", "unspecified"),
             "- Source revision: %s" % metadata.get("source_revision", "unspecified"),
             "", "## FPGA Resources", "", "| Resource | Used | Available | Utilization |",
             "|---|---:|---:|---:|"]
    labels = {"slice_luts": "Slice LUTs", "slice_registers": "Slice registers",
              "block_ram_tiles": "Block RAM tiles", "dsps": "DSPs"}
    for key, value in report["resources"].items():
        lines.append("| %s | %d | %d | %.2f%% |" %
                     (labels.get(key, key), value["used"], value["available"],
                      value["utilization_percent"]))
    if report.get("performance"):
        lines += ["", "## Performance", "", "| Stage | Samples | Median (ms) | P95 (ms) | MiB/s |",
                  "|---|---:|---:|---:|---:|"]
        for stage, value in report["performance"].items():
            rate = value["aggregate_mib_per_s"]
            lines.append("| %s | %d | %.3f | %.3f | %s |" %
                         (stage, value["samples"], value["median_ms"], value["p95_ms"],
                          "n/a" if rate is None else "%.3f" % rate))
    timing = report["timing"]
    lines += ["", "## Timing", "", "- WNS: %.3f ns" % timing["wns_ns"],
              "- TNS: %.3f ns" % timing["tns_ns"],
              "- Setup timing met: %s" % ("yes" if timing["timing_met_setup"] else "no"),
              "", "Raw Vivado report files should be archived beside this summary.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--utilization", type=Path, required=True)
    parser.add_argument("--timing", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True,
                        help="JSON with project, board, tool_version, source_revision")
    parser.add_argument("--performance", type=Path,
                        help="JSON array with stage, elapsed_ms, and optional bytes")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    performance = json.loads(args.performance.read_text(encoding="utf-8")) if args.performance else []
    report = build_report(args.utilization.read_text(encoding="utf-8", errors="replace"),
                          args.timing.read_text(encoding="utf-8", errors="replace"),
                          json.loads(args.metadata.read_text(encoding="utf-8")), performance)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "build_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (args.output / "build_report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"passed": report["timing"]["timing_met_setup"],
                      "output": str(args.output)}, indent=2))
    return 0 if report["timing"]["timing_met_setup"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
