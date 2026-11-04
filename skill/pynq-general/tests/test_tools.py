import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, HERE / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


overlay = load_tool("pynq_overlay")
dma_tools = load_tool("dma_helpers")
codegen = load_tool("contract_codegen")
golden = load_tool("compare_golden")
reports = load_tool("vivado_reports")


class FakeBuffer:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def flush(self):
        self.log.append(self.name + ".flush")

    def invalidate(self):
        self.log.append(self.name + ".invalidate")


class FakeChannel:
    def __init__(self, log, name, error=False, idle=True):
        self.log, self.name, self.error, self.idle_value = log, name, error, idle

    def transfer(self, buffer):
        self.log.append(self.name + ".transfer")

    @property
    def idle(self):
        return self.idle_value


class SkillToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_overlay_requires_both_nonempty_artifacts_and_optional_hash_match(self):
        bit = self.root / "design.bit"
        hwh = self.root / "design.hwh"
        bit.write_bytes(b"bitstream")
        hwh.write_bytes(b"metadata")
        result = overlay.validate_artifacts(bit)
        self.assertTrue(result["passed"])
        manifest = self.root / "hashes.json"
        manifest.write_text(json.dumps({key: value for key, value in result["files"].items()}))
        self.assertTrue(overlay.validate_artifacts(bit, manifest=manifest)["passed"])
        bit.write_bytes(b"changed")
        self.assertIn("hash_mismatch_bit", overlay.validate_artifacts(bit, manifest=manifest)["problems"])

    def test_overlay_rejects_missing_or_empty_artifacts(self):
        bit = self.root / "design.bit"
        hwh = self.root / "design.hwh"
        bit.write_bytes(b"bitstream")
        self.assertIn("missing_hwh", overlay.validate_artifacts(bit)["problems"])
        hwh.write_bytes(b"")
        self.assertIn("empty_hwh", overlay.validate_artifacts(bit, hwh)["problems"])

    def test_overlay_probe_checks_expected_ip_and_registers_without_pynq_install(self):
        class RegisterMap:
            def __init__(self):
                self.control = object()
                self.status = object()

        fake = types.SimpleNamespace(Overlay=lambda path, download=False: types.SimpleNamespace(
            ip_dict={"accelerator_0": {}}, accelerator_0=types.SimpleNamespace(register_map=RegisterMap())))
        with patch.dict(sys.modules, {"pynq": fake}):
            result = overlay.probe_overlay(self.root / "x.bit", ["accelerator_0"],
                                           {"accelerator_0": ["control", "status"]})
        self.assertTrue(result["passed"])
        with patch.dict(sys.modules, {"pynq": fake}):
            result = overlay.probe_overlay(self.root / "x.bit", ["missing"],
                                           {"accelerator_0": ["unknown"]})
        self.assertFalse(result["passed"])

    def test_overlay_probe_reads_explicit_read_only_mmio_contract(self):
        calls = []

        class FakeMMIO:
            def __init__(self, base, size):
                calls.append(("open", base, size))

            def read(self, offset):
                calls.append(("read", offset))
                return {0x1C: 0x0005000A, 0x60: 2592}[offset]

        description = {"phys_addr": 0x40000000, "addr_range": 0x1000}
        fake_overlay = types.SimpleNamespace(ip_dict={"image_process_0": description})
        fake_pynq = types.SimpleNamespace(MMIO=FakeMMIO)
        contract = {"ips": {"image_process_0": {"registers": [
            {"name": "version", "offset": 0x1C, "access": "ro", "expected": 0x0005000A},
            {"name": "width", "offset": 0x60, "access": "ro", "expected": 2592}]}}}
        result = overlay.probe_mmio_contract(fake_pynq, fake_overlay, contract)
        self.assertTrue(result["image_process_0"]["passed"])
        self.assertEqual(calls, [("open", 0x40000000, 0x1000), ("read", 0x1C), ("read", 0x60)])
        contract["ips"]["image_process_0"]["registers"][0]["expected"] = 0
        result = overlay.probe_mmio_contract(fake_pynq, fake_overlay, contract)
        self.assertFalse(result["image_process_0"]["passed"])
        contract["ips"]["image_process_0"]["registers"][0].update(expected=0x50000, mask=0xFFFF0000)
        fake_pynq.Overlay = lambda path, download=False: fake_overlay
        with patch.dict(sys.modules, {"pynq": fake_pynq}):
            result = overlay.probe_overlay(self.root / "x.bit", mmio_contract=contract)
        self.assertTrue(result["passed"])

    def test_mmio_contract_checks_all_rows_before_reading_and_reports_read_failure(self):
        calls = []
        fake_pynq = types.SimpleNamespace(MMIO=lambda base, size: calls.append("map"))
        fake_overlay = types.SimpleNamespace(ip_dict={"ip_0": {"phys_addr": 0, "addr_range": 16}})
        rows = [{"name": "status", "offset": 0, "access": "ro"},
                {"name": "bad", "offset": 4, "access": "rw"}]
        with self.assertRaises(ValueError):
            overlay.probe_mmio_contract(fake_pynq, fake_overlay, {"ips": {"ip_0": {"registers": rows}}})
        self.assertEqual(calls, [])

        class BrokenMMIO:
            def read(self, offset):
                raise OSError("unavailable")

        fake_pynq.MMIO = lambda base, size: BrokenMMIO()
        result = overlay.probe_mmio_contract(fake_pynq, fake_overlay,
                    {"ips": {"ip_0": {"registers": rows[:1]}}})
        self.assertFalse(result["ip_0"]["passed"])
        self.assertIn("mmio_read_failed", result["ip_0"]["registers"][0]["reason"])

    def test_mmio_contract_rejects_writes_unaligned_or_out_of_range_offsets(self):
        fake_pynq = types.SimpleNamespace(MMIO=lambda base, size: None)
        fake_overlay = types.SimpleNamespace(ip_dict={"ip_0": {"phys_addr": 0, "addr_range": 16}})
        for register in ({"name": "cmd", "offset": 0, "access": "rw"},
                         {"name": "status", "offset": 2, "access": "ro"},
                         {"name": "status", "offset": 16, "access": "ro"}):
            with self.subTest(register=register), self.assertRaises(ValueError):
                overlay.probe_mmio_contract(fake_pynq, fake_overlay,
                    {"ips": {"ip_0": {"registers": [register]}}})
        with self.assertRaises(ValueError):
            overlay.probe_mmio_contract(fake_pynq, fake_overlay, {"ips": {}})

    def test_overlay_pair_must_share_stem_and_directory(self):
        bit = self.root / "design.bit"
        hwh = self.root / "other.hwh"
        bit.write_bytes(b"bit")
        hwh.write_bytes(b"hwh")
        result = overlay.validate_artifacts(bit, hwh)
        self.assertIn("bit_hwh_pair_mismatch", result["problems"])

    def test_dma_cache_order_rx_before_tx_and_invalidate_after_completion(self):
        log = []
        rx, tx = FakeBuffer(log, "rx"), FakeBuffer(log, "tx")
        dma = types.SimpleNamespace(recvchannel=FakeChannel(log, "rx"),
                                    sendchannel=FakeChannel(log, "tx"))
        result = dma_tools.transfer(dma, tx_buffer=tx, rx_buffer=rx)
        self.assertEqual(log, ["rx.flush", "rx.transfer", "tx.flush", "tx.transfer", "rx.invalidate"])
        self.assertTrue(result["completed"])

    def test_dma_timeout_and_reported_channel_error_are_not_silent(self):
        dma = types.SimpleNamespace(recvchannel=FakeChannel([], "rx", idle=False),
                                    sendchannel=FakeChannel([], "tx"))
        with self.assertRaises(TimeoutError):
            dma_tools.transfer(dma, rx_buffer=FakeBuffer([], "rx"), timeout_s=0.005,
                               poll_interval_s=0.001)
        dma.recvchannel = FakeChannel([], "rx", error=True)
        with self.assertRaises(dma_tools.DmaTransferError):
            dma_tools.transfer(dma, rx_buffer=FakeBuffer([], "rx"))

    def test_contract_codegen_generates_guarded_accessors_and_mock_tests(self):
        contract = {"registers": [{"name": "status", "offset": 0, "access": "ro"},
                                  {"name": "command", "offset": 4, "access": "wo"}]}
        module_path, test_path = codegen.generate(contract, self.root / "generated")
        sys.path.insert(0, str(module_path.parent))
        self.addCleanup(sys.path.remove, str(module_path.parent))
        import generated_registers
        class MMIO:
            values = {0: 17}
            def read(self, offset): return self.values[offset]
            def write(self, offset, value): self.values[offset] = value
        self.assertEqual(generated_registers.read_status(MMIO()), 17)
        with self.assertRaises(AttributeError):
            generated_registers.write_status(MMIO(), 1)
        with self.assertRaises(ValueError):
            generated_registers.write_command(MMIO(), -1)
        self.assertTrue(test_path.is_file())
        with self.assertRaises(ValueError):
            codegen.validate({"registers": [{"name": "bad", "offset": 2, "access": "rw"}]})

    def test_golden_binary_exact_and_numeric_tolerance(self):
        ref, actual = self.root / "ref.bin", self.root / "actual.bin"
        ref.write_bytes(bytes([1, 2, 3]))
        actual.write_bytes(bytes([1, 2, 4]))
        self.assertFalse(golden.compare(ref, actual)["passed"])
        ref.write_bytes(bytes([0, 0, 128, 63]))
        actual.write_bytes(bytes([1, 0, 128, 63]))
        result = golden.compare(ref, actual, "f32le", atol=0.0001)
        self.assertTrue(result["passed"])
        self.assertEqual(result["mismatches"], 0)
        actual.write_bytes(bytes([1, 0, 128, 63, 0, 0, 0, 0]))
        self.assertEqual(golden.compare(ref, actual, "f32le")["reason"], "size_mismatch")
        ref.write_bytes(bytes([0, 0, 192, 127]))  # NaN in little-endian float32.
        actual.write_bytes(bytes([0, 0, 192, 127]))
        result = golden.compare(ref, actual, "f32le")
        self.assertFalse(result["passed"])
        self.assertEqual(result["mismatches"], 1)

    def test_vivado_report_parser_extracts_resources_timing_and_performance(self):
        utilization = """
| Slice LUTs*        | 1,234 | 53,200 | 2.32 |
| Slice Registers    | 2,345 | 106,400 | 2.20 |
| Block RAM Tile     | 12 | 140 | 8.57 |
| DSPs               | 8 | 220 | 3.64 |
"""
        timing = """
WNS(ns) TNS(ns) TNS Failing Endpoints
------- ------- --------------------
0.125 0.000 0
"""
        metadata = {"project": "test", "board": "part", "tool_version": "v", "source_revision": "abc"}
        report = reports.build_report(utilization, timing, metadata,
                                      [{"stage": "dma", "elapsed_ms": 4.0, "bytes": 4000},
                                       {"stage": "dma", "elapsed_ms": 6.0, "bytes": 6000}])
        self.assertEqual(report["resources"]["slice_luts"]["used"], 1234)
        self.assertTrue(report["timing"]["timing_met_setup"])
        self.assertEqual(report["performance"]["dma"]["median_ms"], 5.0)
        self.assertIn("Performance", reports.render_markdown(report))
        with self.assertRaises(ValueError):
            reports.parse_utilization("unrecognized format")

    def test_board_record_template_cannot_be_mistaken_for_a_pass(self):
        record = json.loads((HERE / "examples" / "board_test_record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], "pynq-board-test-record/v1")
        self.assertEqual(record["status"], "incomplete")
        self.assertTrue(record["identity"]["source_revision"].startswith("FILL_ME"))
        self.assertTrue(all(test["result"] == "not_run" for test in record["tests"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
