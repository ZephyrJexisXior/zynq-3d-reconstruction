# General PYNQ Skill

This is a board-neutral PYNQ/Zynq workflow for checking an Overlay, agreeing an
AXI-Lite register contract, moving data with a simple AXI DMA, comparing a PL
result with a software reference, and collecting reproducible build evidence.
It is deliberately independent of the source project's camera, turntable,
image format, IP names, and reconstruction pipeline.

## 中文使用入口

这是包含 `SKILL.md`、工具、示例、测试和板测记录的完整目录。
在 Codex 中可直接指定：

> 使用这个目录中的 SKILL.md 完成 PYNQ 检查；以当前板端读回数据为准。

指定入口文件的完整路径即可使用。`$pynq-general` 简写需要先让当前
Codex 环境发现这个 Skill，目录存在不代表已安装到技能列表。

复制整个 `pynq-general` 文件夹后，在该文件夹内运行下面的离线命令：

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
python tools/contract_codegen.py examples/interface_contract.json --output-dir generated
python -m unittest discover -s generated -p 'test_*.py' -v
```

工具使用 Python 标准库，建议 Python 3.8 及以上；上板探测另需目标板的
PYNQ 环境和 MMIO 读取权限。生成目录可留在运行工作区，无需随 Skill 分发。

当前验证：13 项工具测试、6 项生成访问器测试通过；Skill Creator
结构校验通过。新版只读 MMIO 探测已在 PYNQ 2.7.0 板端通过，详见
[原始探测结果](references/board_probe_20261104.json)。通用 AXI DMA helper
与 golden-vector 对比尚未完成板测；本项目 VDMA 使用既有应用驱动。
这些限制不会阻止使用已验证的离线工具及只读板端探测功能。

## Applicability at a glance

| Item | Applicable when | How to use | Verified effect in this package | Fails or stops when | Evidence/source |
|---|---|---|---|---|---|
| Overlay artifact check and probe | A PYNQ design is delivered as a `.bit` plus matching `.hwh` | Run `tools/pynq_overlay.py <design.bit>` for offline pair/hash checks; add `--probe --ip <name> --registers registers.json` on the target board | Desktop mock tests detect missing files, empty files, stem/directory mismatch, hash mismatch, missing IPs, and missing register names | Hashes cannot prove electrical safety, constraints, or functional compatibility; probing needs the target PYNQ runtime | `tools/pynq_overlay.py`; source board startup rejects wrong Overlay/version/dimensions |
| `register_map` contract and code generation | Host code and PL registers need a reviewable, repeatable interface | Edit `examples/interface_contract.json`; run `contract_codegen.py CONTRACT --output-dir generated`; run the generated unittest | The sample contract produces guarded read/write accessors and mock tests; invalid names, offsets, access modes, and values are rejected | HWH metadata may not expose usable names; explicit MMIO offsets and access semantics are then required | `tools/contract_codegen.py`; source app checks discovered IP/registers before capture |
| Explicit-offset MMIO probe | PYNQ/HWH does not expose a usable `register_map`, but the design has a reviewed register map | Run `pynq_overlay.py BIT --probe --mmio-contract examples/mmio_read_contract.json` on the target; edit the sample IP names, offsets, expected values, and masks for the current design | Mock tests prove only documented `ro` offsets are read and that masked expectations are checked | This is a read-only identity/status probe, not a register driver; even reads can have IP-specific side effects | `tools/pynq_overlay.py`; [observed board run](references/observed_board_run.md) showed PYNQ 2.7.0 lacks `register_map` for the custom IP while documented MMIO reads worked |
| Cache-aware simple AXI DMA | PYNQ uses a simple send/receive DMA and buffers are allocated by a supported PYNQ allocator | Call `dma_helpers.transfer(dma, tx_buffer=..., rx_buffer=..., timeout_s=...)`; arm RX before TX | Mock tests verify flush/transfer/invalidate order, completion, timeout, and reported channel error handling | It is not a VDMA frame-store driver, custom DMA driver, or proof of payload framing; coherent buffers may need different synchronization | `tools/dma_helpers.py`; source path flushes/invalidate buffers and bounds polling |
| AXI VDMA integration | The design uses frame-based S2MM/MM2S VDMA | Follow `references/vdma_integration.md`; use a PYNQ video/frame API supported by the installed release or a design-specific tested adapter | This package has no generic VDMA transfer helper; board evidence is limited to the deployed application's existing capture path | VDMA frame stores, stride, circular/parking mode, genlock, frame sync, and completion semantics are design-specific | PYNQ version/API and `.hwh` plus the deployed application contract |
| Golden-reference comparison | A deterministic PL output can be compared with software or a checked-in reference | Run `compare_golden.py REF ACTUAL --format bytes|u8|u16le|i16le|f32le [--atol A --rtol R] --json report.json` | Tests cover exact byte comparison, numeric tolerance, size mismatch, and finite-value handling | A passing comparison only covers the supplied vector; it does not prove timing, frame sync, or visual quality | `tools/compare_golden.py`; source tests preserve pixel/color integrity |
| Vivado resource/timing/performance report | A reproducible Vivado build has text utilization/timing reports and measured runtime samples | Run `vivado_reports.py --utilization ... --timing ... --metadata ... [--performance ...] --output reports/summary` | Sample reports parse LUT/register/BRAM/DSP, WNS/TNS, and performance summaries; unrecognized required rows fail loudly | Report layout/localization or missing constraints can make parsing invalid; performance JSON is measurement input, not fabricated evidence | `tools/vivado_reports.py`; source build emits utilization/timing/DRC reports |
| PL/PS partition prompt | An algorithm must be split between RTL/PL and Python/C/PS | Fill bracketed inputs in `prompts/partition_algorithm.md` and attach measured constraints | The template forces throughput, memory, precision, data movement, cache, golden vector, and falsification questions | It cannot estimate resources or prove feasibility without synthesis/timing/board data | Prompt is distilled from recurring source-project design decisions; it is a planning aid, not a benchmark |
| Timing/resource triage prompt | A complete post-synthesis/post-implementation report has a failure | Use `prompts/triage_reports.md` with exact part, tool version, reports, and source revision | The template requires exact failing rows, evidence/hypothesis separation, expected metric change, and regression checks | A screenshot or summary without constraints/path context is insufficient; the prompt does not edit RTL | Prompt is distilled from the source build/debug workflow |
| PYNQ/PL-PS pitfalls checklist | A transfer or capture fails and the failing boundary is not known | Read `references/pitfalls.md` and check rows in order: artifact, IP/register, DMA, cache, framing, dimensions, timing | The checklist records triggers, symptoms, diagnosis, mitigation, and the source observation behind each item | Board-specific pinouts, clocks, USB behavior, and VDMA semantics still require the target design's contract | Checklist entries cite source code/tests/observations; they are hypotheses to verify on the new board |
| Board validation and evidence record | The design has been deployed to a physical target and results must be reproducible | Follow `references/board_validation.md`; copy `examples/board_test_record.json`, replace every `FILL_ME`, and retain raw logs/reports beside it | The runbook separates static artifact checks, non-programming probe, programmed-overlay tests, data-path checks, and measured performance; record is a schema example, not test evidence | A template with placeholders is incomplete; desktop mocks, screenshots without raw data, or an unverified artifact pair do not count as a target-board pass | `references/board_validation.md`; required evidence follows the contest-ready project package checklist |

## Quick start

From the repository root:

```powershell
python skill/pynq-general/tools/contract_codegen.py `
  skill/pynq-general/examples/interface_contract.json `
  --output-dir .\generated
python -m unittest discover -s .\generated -p 'test_*.py'
python -m unittest discover -s skill/pynq-general/tests -p 'test_*.py'
```

For a board Overlay, first validate the artifact pair and only then use the
target's PYNQ image:

```powershell
python skill/pynq-general/tools/pynq_overlay.py .\build\design.bit `
  --manifest .\build\overlay-manifest.json
python skill/pynq-general/tools/pynq_overlay.py .\build\design.bit `
  --probe --ip accelerator_0 --registers .\registers.json
python skill/pynq-general/tools/pynq_overlay.py .\build\design.bit `
  --probe --mmio-contract .\mmio_read_contract.json
```

The probe inspects by default. Add `--download` only when programming the
board is an intentional part of the test. A typical PYNQ access pattern is:
`ip = overlay.<ip_name>; regs = ip.register_map; value = regs.<register>`.
Use names and access permissions from the current HWH/interface contract. If
`register_map` is missing, use the explicit read-only MMIO contract with offsets
verified in the current design's source or register documentation; do not guess
offsets. The probe never programs the bitstream unless `--download` is supplied.

For a complete target-board run, use the ordered checks and evidence record in
`references/board_validation.md`. Do not mark a test complete until the JSON
record contains actual board/build identity and measured results, and its raw
evidence files are retained.

## Boundaries and portability

The scripts use the Python standard library. PYNQ is imported only by the
optional board probe, so the offline tests run on a desktop. A new team must
provide its board part, PYNQ version, Overlay pair, IP names, register contract,
DMA direction, pixel/word geometry, and clock/throughput requirements. Replace
the sample contract and reports rather than copying the source project's
identifiers.

`dma_helpers.py` targets PYNQ's simple AXI DMA channel API. VDMA frame stores,
custom DMA, Linux USB capture, and coherent ports need a design-specific
adapter. Do not use the simple AXI DMA helper with VDMA. A successful DMA transfer does not establish line stride, pixel
ordering, frame synchronization, or timing closure. The Overlay hash check
also cannot prove that a bitstream is safe or built from the same source unless
the release process supplies a trusted build ID/manifest.

## Evidence and limitations

The supplied tests use fake PYNQ/MMIO/DMA objects and deterministic temporary
files. They verify observable error handling and call order, not electrical
I/O. The [observed board run](references/observed_board_run.md) is evidence for
one read-only probe of one deployed configuration; it does not complete the
generic DMA/golden-vector tests. The source project supplied the observations behind the checklist:
artifact/version and dimension checks mattered; stale cache contents could be
read as a new frame; a circular VDMA store could advance without an IOC bit;
clock assumptions changed timing; and software USB timestamps did not prove
exposure synchronization. These observations motivate the checks but are not
universal hardware guarantees.

Save the `.bit`, `.hwh`, trusted hashes, source revision, raw Vivado reports,
generated report, and board test log together for a release. Keep desktop test
results separate from target-board results.

## Board test record

`examples/board_test_record.json` is an intentionally incomplete record. Copy
it into the test output directory and replace all `FILL_ME` values with
observed values. Use `null` only when a measurement does not apply and explain
why in `notes`; do not infer measurements from source settings or older runs.
The JSON is useful for machine-readable archiving, while the board log and raw
reports remain the primary evidence.
