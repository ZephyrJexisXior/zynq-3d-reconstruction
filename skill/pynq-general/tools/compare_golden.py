"""Compare binary outputs exactly or element-wise with a numeric tolerance."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path


FORMATS = {"u8": ("B", 1), "u16le": ("<H", 2), "i16le": ("<h", 2),
           "f32le": ("<f", 4)}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def compare(reference: Path, actual: Path, fmt: str = "bytes",
            atol: float = 0.0, rtol: float = 0.0) -> dict:
    reference, actual = Path(reference), Path(actual)
    if not reference.is_file() or not actual.is_file():
        raise FileNotFoundError("both reference and actual files must exist")
    if atol < 0 or rtol < 0:
        raise ValueError("tolerances must be nonnegative")
    if fmt == "bytes":
        ref_size, act_size = reference.stat().st_size, actual.stat().st_size
        equal = ref_size == act_size and reference.read_bytes() == actual.read_bytes()
        return {"passed": equal, "format": fmt, "reference_bytes": ref_size,
                "actual_bytes": act_size, "elements": min(ref_size, act_size),
                "mismatches": 0 if equal else None, "max_abs_error": 0 if equal else None,
                "reference_sha256": digest(reference), "actual_sha256": digest(actual)}

    if fmt not in FORMATS:
        raise ValueError("format must be bytes or one of %s" % ", ".join(FORMATS))
    code, width = FORMATS[fmt]
    ref_size, act_size = reference.stat().st_size, actual.stat().st_size
    if ref_size % width or act_size % width:
        raise ValueError("file size must be a multiple of the selected element size")
    if ref_size != act_size:
        return {"passed": False, "format": fmt, "reference_bytes": ref_size,
                "actual_bytes": act_size, "elements": min(ref_size, act_size),
                "mismatches": None, "reason": "size_mismatch",
                "reference_sha256": digest(reference), "actual_sha256": digest(actual)}
    element = struct.Struct(code)
    mismatches, max_error, error_sum, count = 0, 0.0, 0.0, 0
    block_bytes = (1024 * 1024 // width) * width
    with reference.open("rb") as left, actual.open("rb") as right:
        while True:
            a, b = left.read(block_bytes), right.read(block_bytes)
            if not a and not b:
                break
            for offset in range(0, len(a), width):
                expected = element.unpack_from(a, offset)[0]
                observed = element.unpack_from(b, offset)[0]
                if not math.isfinite(expected) or not math.isfinite(observed):
                    error = math.inf
                    accepted = False
                else:
                    error = abs(observed - expected)
                    accepted = error <= atol + rtol * abs(expected)
                count += 1
                error_sum += error if math.isfinite(error) else 0.0
                max_error = max(max_error, error)
                mismatches += not accepted
    return {"passed": mismatches == 0, "format": fmt, "elements": count,
            "mismatches": mismatches, "max_abs_error": max_error,
            "mean_abs_error": error_sum / count if count else 0.0,
            "atol": atol, "rtol": rtol,
            "reference_sha256": digest(reference), "actual_sha256": digest(actual)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("actual", type=Path)
    parser.add_argument("--format", choices=("bytes", *FORMATS), default="bytes")
    parser.add_argument("--atol", type=float, default=0.0)
    parser.add_argument("--rtol", type=float, default=0.0)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = compare(args.reference, args.actual, args.format, args.atol, args.rtol)
    output = json.dumps(report, indent=2)
    if args.json:
        args.json.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
