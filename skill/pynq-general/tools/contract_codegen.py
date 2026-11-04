"""Generate checked MMIO accessors and a unittest skeleton from JSON."""
from __future__ import annotations

import argparse
import json
import keyword
from pathlib import Path


def validate(contract: dict) -> list[dict]:
    if not isinstance(contract, dict) or not isinstance(contract.get("registers"), list):
        raise ValueError("contract must contain a registers array")
    rows = contract["registers"]
    names, offsets = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each register must be an object")
        name, offset, access = row.get("name"), row.get("offset"), row.get("access")
        if (not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name)
                or name in names):
            raise ValueError("register names must be unique Python identifiers")
        if (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
                or offset > 0xFFFFFFFF or offset % 4):
            raise ValueError("register offsets must be aligned unsigned 32-bit values")
        if offset in offsets:
            raise ValueError("register offsets must be unique")
        if access not in ("ro", "wo", "rw"):
            raise ValueError("register access must be ro, wo, or rw")
        names.add(name)
        offsets.add(offset)
    return sorted(rows, key=lambda row: row["offset"])


def generate(contract: dict, output_dir: Path) -> tuple[Path, Path]:
    rows = validate(contract)
    output_dir.mkdir(parents=True, exist_ok=True)
    module = output_dir / "generated_registers.py"
    tests = output_dir / "test_generated_registers.py"
    table = {row["name"]: {"offset": row["offset"], "access": row["access"]}
             for row in rows}
    lines = ['"""Generated MMIO accessors; edit the contract, not this file."""',
             "REGISTERS = %r" % table, "",
             "def _check_value(value):", "    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF:",
             "        raise ValueError('MMIO values must be unsigned 32-bit integers')", ""]
    for row in rows:
        name = row["name"]
        if row["access"] in ("ro", "rw"):
            lines.extend(["def read_%s(mmio):" % name,
                          "    return mmio.read(REGISTERS[%r]['offset'])" % name, ""])
        if row["access"] in ("wo", "rw"):
            lines.extend(["def write_%s(mmio, value):" % name,
                          "    _check_value(value)",
                          "    mmio.write(REGISTERS[%r]['offset'], value)" % name, ""])
    module.write_text("\n".join(lines), encoding="utf-8")
    test_lines = ["import unittest", "import generated_registers as regs", "",
                  "class FakeMMIO:", "    def __init__(self): self.values = {}",
                  "    def read(self, offset): return self.values.get(offset, 0)",
                  "    def write(self, offset, value): self.values[offset] = value", "",
                  "class GeneratedRegisterTests(unittest.TestCase):",
                  "    def setUp(self): self.mmio = FakeMMIO()"]
    for row in rows:
        name = row["name"]
        if row["access"] in ("ro", "rw"):
            test_lines.extend(["", "    def test_read_%s_uses_declared_offset(self):" % name,
                               "        self.mmio.values[%d] = 0x1234" % row["offset"],
                               "        self.assertEqual(regs.read_%s(self.mmio), 0x1234)" % name])
        if row["access"] in ("wo", "rw"):
            test_lines.extend(["", "    def test_write_%s_uses_declared_offset(self):" % name,
                               "        regs.write_%s(self.mmio, 0x1234)" % name,
                               "        self.assertEqual(self.mmio.values[%d], 0x1234)" % row["offset"]])
    test_lines.extend(["", "if __name__ == '__main__':", "    unittest.main()", ""])
    tests.write_text("\n".join(test_lines), encoding="utf-8")
    return module, tests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    module, tests = generate(contract, args.output_dir)
    print(json.dumps({"generated": [str(module), str(tests)]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
