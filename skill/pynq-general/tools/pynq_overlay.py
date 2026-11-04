"""Validate a PYNQ Overlay release and optionally probe it on a board."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_artifacts(bitfile: Path, hwh: Optional[Path] = None,
                       manifest: Optional[Path] = None) -> dict:
    bitfile = Path(bitfile)
    hwh = Path(hwh) if hwh else bitfile.with_suffix(".hwh")
    problems = []
    if bitfile.parent.resolve() != hwh.parent.resolve() or bitfile.stem != hwh.stem:
        problems.append("bit_hwh_pair_mismatch")
    for label, path in (("bit", bitfile), ("hwh", hwh)):
        if not path.is_file():
            problems.append("missing_%s" % label)
        elif path.stat().st_size == 0:
            problems.append("empty_%s" % label)
    files = {}
    if not problems:
        files = {"bit": {"path": str(bitfile), "bytes": bitfile.stat().st_size,
                          "sha256": sha256(bitfile)},
                 "hwh": {"path": str(hwh), "bytes": hwh.stat().st_size,
                          "sha256": sha256(hwh)}}
    if manifest:
        try:
            expected = json.loads(Path(manifest).read_text(encoding="utf-8"))
            for key in ("bit", "hwh"):
                if key not in expected or key not in files:
                    problems.append("manifest_missing_%s" % key)
                elif expected[key].get("sha256") != files[key]["sha256"]:
                    problems.append("hash_mismatch_%s" % key)
        except (OSError, ValueError) as exc:
            problems.append("invalid_manifest: %s" % exc)
    return {"passed": not problems, "files": files, "problems": problems}


def _public_register_names(register_map) -> set[str]:
    names = set()
    for name in dir(register_map):
        if name.startswith("_"):
            continue
        try:
            value = getattr(register_map, name)
        except Exception:
            continue
        if not callable(value):
            names.add(name)
    return names


def probe_overlay(bitfile: Path, expected_ips: Iterable[str] = (),
                  expected_registers: Optional[dict] = None,
                  download: bool = False,
                  mmio_contract: Optional[dict] = None) -> dict:
    """Load and inspect an Overlay; call only on the target PYNQ board."""
    import pynq

    overlay = pynq.Overlay(str(bitfile), download=download)
    available = set(overlay.ip_dict)
    missing_ips = sorted(set(expected_ips) - available)
    missing_registers = {}
    for ip_name, registers in (expected_registers or {}).items():
        if ip_name not in available:
            missing_registers[ip_name] = sorted(registers)
            continue
        try:
            register_map = getattr(getattr(overlay, ip_name), "register_map")
        except Exception:
            missing_registers[ip_name] = sorted(registers)
            continue
        absent = sorted(set(registers) - _public_register_names(register_map))
        if absent:
            missing_registers[ip_name] = absent
    mmio_results = {}
    if mmio_contract is not None:
        mmio_results = probe_mmio_contract(pynq, overlay, mmio_contract)
    mmio_passed = all(row["passed"] for row in mmio_results.values())
    return {"bitfile": str(bitfile), "available_ips": sorted(available),
            "missing_ips": missing_ips, "missing_registers": missing_registers,
            "mmio": mmio_results,
            "passed": not missing_ips and not missing_registers and mmio_passed}


def probe_mmio_contract(pynq_module, overlay, contract: dict) -> dict:
    """Read explicit, documented read-only offsets without register_map."""
    if (not isinstance(contract, dict) or not isinstance(contract.get("ips"), dict)
            or not contract["ips"]):
        raise ValueError("MMIO contract must contain a non-empty ips object")
    results = {}
    for ip_name, ip_contract in contract["ips"].items():
        registers = ip_contract.get("registers") if isinstance(ip_contract, dict) else None
        if not isinstance(registers, list) or not registers or ip_name not in overlay.ip_dict:
            results[ip_name] = {"passed": False, "reason": "missing_ip_or_registers"}
            continue
        description = overlay.ip_dict[ip_name]
        try:
            base = int(description["phys_addr"])
            size = int(description["addr_range"])
            if base < 0 or size <= 0:
                raise ValueError("invalid base address or range")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            results[ip_name] = {"passed": False, "reason": "invalid_ip_address_range: %s" % exc}
            continue
        # Validate every row before mapping or reading any register in this IP.
        names, offsets = set(), set()
        for register in registers:
            if not isinstance(register, dict):
                raise ValueError("MMIO register rows must be objects")
            name, offset = register.get("name"), register.get("offset")
            access = register.get("access")
            if (not isinstance(name, str) or not name or not isinstance(offset, int)
                    or isinstance(offset, bool) or offset < 0 or offset % 4
                    or offset + 4 > size or access != "ro" or name in names or offset in offsets):
                raise ValueError("MMIO registers require a name, aligned in-range offset, and access=ro")
            expected = register.get("expected")
            if expected is not None and (not isinstance(expected, int)
                                         or isinstance(expected, bool)
                                         or not 0 <= expected <= 0xFFFFFFFF):
                raise ValueError("expected MMIO values must be unsigned 32-bit integers or null")
            mask = register.get("mask", 0xFFFFFFFF)
            if (not isinstance(mask, int) or isinstance(mask, bool)
                    or not 0 <= mask <= 0xFFFFFFFF):
                raise ValueError("MMIO comparison masks must be unsigned 32-bit integers")
            names.add(name)
            offsets.add(offset)
        rows = []
        try:
            mmio = pynq_module.MMIO(base, size)
        except Exception as exc:
            results[ip_name] = {"passed": False, "reason": "mmio_mapping_failed: %s" % exc}
            continue
        for register in registers:
            name, offset = register["name"], register["offset"]
            expected, mask = register.get("expected"), register.get("mask", 0xFFFFFFFF)
            try:
                value = int(mmio.read(offset)) & 0xFFFFFFFF
            except Exception as exc:
                rows.append({"name": name, "offset": offset, "passed": False,
                             "reason": "mmio_read_failed: %s" % exc})
                break
            matched = expected is None or (value & mask) == (expected & mask)
            rows.append({"name": name, "offset": offset, "value": value,
                         "expected": expected, "mask": mask, "passed": matched})
        results[ip_name] = {"passed": all(row["passed"] for row in rows), "registers": rows}
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bitfile", type=Path)
    parser.add_argument("--hwh", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--probe", action="store_true",
                        help="inspect the Overlay; requires the target PYNQ board")
    parser.add_argument("--download", action="store_true",
                        help="program the bitstream during the probe")
    parser.add_argument("--ip", action="append", default=[])
    parser.add_argument("--registers", type=Path,
                        help="JSON object mapping IP names to register names")
    parser.add_argument("--mmio-contract", type=Path,
                        help="JSON explicit read-only MMIO offsets for PYNQ versions without register_map")
    args = parser.parse_args()
    if args.download and not args.probe:
        parser.error("--download requires --probe")
    result = validate_artifacts(args.bitfile, args.hwh, args.manifest)
    if result["passed"] and args.probe:
        registers = json.loads(args.registers.read_text(encoding="utf-8")) if args.registers else {}
        mmio_contract = json.loads(args.mmio_contract.read_text(encoding="utf-8")) if args.mmio_contract else None
        result["probe"] = probe_overlay(args.bitfile, args.ip, registers, args.download,
                                        mmio_contract)
        result["passed"] = result["probe"]["passed"]
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
