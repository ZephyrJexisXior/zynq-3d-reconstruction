# Target-Board Validation

Use this procedure for one explicitly identified board and one exact Overlay
release. It produces evidence for a single tested configuration; it does not
certify other boards, bitstreams, clocks, or buffer geometries.

## Inputs

Before connecting or programming hardware, identify and record:

- Board model, FPGA part, PYNQ image/version, Vivado/Vitis version, and source revision.
- The `.bit` and same-build `.hwh` paths and SHA-256 hashes.
- Expected IP names, register names/access/offsets, version or capability value, and address map.
- DMA type and direction, buffer allocator, stream format, dimensions, stride, transfer length, and clock assumptions.
- A deterministic input vector and independently generated software reference, including file hashes and numeric tolerance.
- A board-safe recovery method. Check reset behavior and ensure no actuator can move unexpectedly during probing.

If any interface or recovery detail is unknown, stop before programming. Do not
guess register offsets or transfer sizes from another design.

## Ordered run

1. **Archive identity.** Copy the `.bit`, `.hwh`, release manifest, source revision, interface contract, and raw synthesis/implementation reports into a new run directory. Generate hashes with `pynq_overlay.py` or a trusted release tool. The pair must have the same stem and directory; compare both hashes to the release manifest.
2. **Run desktop checks.** Run the skill's unit tests, code generation test, and offline golden comparison using the exact vector intended for the board. Record these separately; they do not count as board results.
3. **Probe without programming.** On the target PYNQ image, invoke `pynq_overlay.py` with `--probe` and expected IP names. Use the JSON register-name map when PYNQ exposes `register_map`; otherwise use `--mmio-contract` with offsets reviewed against this exact design. Include only documented safe-to-read registers marked `ro`. Do not add `--download` unless changing the programmed Overlay is explicitly intended. Save the JSON output and console log.
4. **Program and inspect.** If the target is safe to reprogram, run a separate command with `--probe --download`. Verify the expected IPs/registers and read a documented version/capability register where available. Compare readback values to the contract. Do not start a streaming transfer yet.
5. **Run a bounded known-vector transfer.** First select a data path matching the design: simple AXI DMA uses the helper's send/receive API; AXI VDMA uses the frame-store procedure and stride contract in `vdma_integration.md`; custom movers require their own reviewed adapter. Allocate buffers with the target-supported contiguous allocator. Prepare exact-length data, arm receive before transmit for a simple streaming AXI DMA path, synchronize cache, wait with a deadline, inspect documented status/error registers, and retain buffers until hardware no longer owns them. Save input, output, status, and software comparison JSON.
6. **Repeat and measure.** Perform enough transfers to expose intermittent failures. Record each elapsed time with the measurement boundary, byte count, warm-up policy, and any rejected run. Do not report averages without sample count and raw samples.
7. **Build the summary.** Parse the raw Vivado utilization/timing reports and measured performance samples with `vivado_reports.py`. Keep generated JSON/Markdown beside the original reports. A timing pass requires nonnegative WNS and no failing setup paths in the actual report; inspect hold timing, unconstrained paths, and DRC separately.
8. **Complete the record.** Copy `examples/board_test_record.json`, replace all `FILL_ME` values, set each test to `passed`, `failed`, or `not_applicable`, and link every result to retained evidence. Set the overall status to `passed` only if all required checks passed. A skipped or unexecuted requirement keeps the status `incomplete`.

## Evidence directory

Keep one immutable directory per run, for example:

```text
validation/<run-id>/
  board_test_record.json
  source_revision.txt
  overlay-manifest.json
  design.bit
  design.hwh
  interface_contract.json
  logs/
    host-test.log
    overlay-probe.json
    board-console.log
  vectors/
    input.bin
    reference.bin
    actual.bin
    comparison.json
  reports/
    utilization.rpt
    timing_summary.rpt
    drc.rpt
    build_report.json
    build_report.md
```

Record hashes for artifacts and vectors. Preserve failed attempts and explain
re-runs rather than replacing evidence silently. Do not commit private paths,
credentials, user data, or vendor files that the project is not permitted to
redistribute.

## Pass and stop conditions

- **Pass:** exact artifact identity is recorded; IP/register checks match the contract; deterministic output meets the declared comparison rule; transfer completes within the deadline without channel errors; repeated measurements and build reports are retained; the test record links to the raw evidence.
- **Fail:** wrong/missing artifacts or IPs, register mismatch, DMA error/timeout, data-size mismatch, golden mismatch, negative setup slack, or an unrepeatable result. Record the first failing boundary and stop dependent tests.
- **Incomplete:** no physical board run, missing raw evidence, skipped check, unknown board identity, or a result derived only from mocks, simulation, source configuration, screenshots, or an older run.

This flow is for evidence collection and diagnosis. It does not validate
electrical limits, pin safety, camera exposure synchronization, visual quality,
functional safety, or any design-specific property absent from the contract.
