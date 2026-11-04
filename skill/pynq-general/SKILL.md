---
name: pynq-general
description: Build, inspect, and validate reusable PYNQ Overlay, AXI-Lite register, DMA, and board/host interface workflows. Use for PYNQ projects across tasks and boards; adapt hardware-specific IP names, contracts, and buffer geometry before running.
---

# General PYNQ Workflow

Use this skill when a project needs a PYNQ host-side path for loading an Overlay, accessing an AXI-Lite IP, transferring buffers with DMA, or validating a host/PL contract.

1. Read the project's generated HWH and interface contract before writing MMIO code. Confirm IP names, address ranges, register offsets/access, stream format, dimensions, stride, and DMA direction.
2. Check the `.bit` and `.hwh` pair and any release hashes. Inspect the intended Overlay without programming it, then verify required IPs and a documented hardware version or capability register. Prefer PYNQ `register_map` when available; if it is unavailable, use the explicit read-only MMIO contract path in `tools/pynq_overlay.py` with offsets taken from the current design source or authoritative register documentation. Never guess offsets.
3. Identify the exact data mover before handling buffers. For simple AXI DMA only, allocate physically contiguous PYNQ buffers, configure receive before transmit for stream pipelines, flush PS-written inputs before PL reads, wait with a deadline, check status/error registers, and invalidate PL-written outputs before PS reads. Keep the buffers alive until the channel is idle or reset. For AXI VDMA, use the design's frame-store/stride/parking/circular-buffer contract and a VDMA-capable API or application adapter; `dma_helpers.transfer` does not support VDMA.
4. Compare a deterministic PL result with a software golden reference. Record actual build reports and measured board results separately from simulation or desktop tests.
5. On failure, capture the first failing boundary: artifact identity, IP discovery, register readback, DMA status, frame/transfer length, cache visibility, or numeric comparison. See [pitfalls.md](references/pitfalls.md).
6. Follow [board_validation.md](references/board_validation.md) for ordered target checks. Save a completed copy of [board_test_record.json](examples/board_test_record.json) with raw logs, reports, artifact hashes, and source revision; never fill a record from expected settings or prior-run results.

## Boundaries

This skill does not infer board pinouts, clock rates, register layouts, cacheability, or DMA semantics. It does not make a design PYNQ-compatible merely because the target contains a Zynq PS. AXI VDMA, simple AXI DMA, custom DMA, and Linux-managed USB are different interfaces and must be checked against the actual design. The MMIO probe reads only contract entries explicitly marked `ro`; even a read can have hardware-specific side effects, so only include documented safe-to-read registers.

Read [README.md](README.md) for applicability, commands, evidence, and failure conditions. Use [interface_contract.json](examples/interface_contract.json) when generating a register accessor/test skeleton, and [mmio_read_contract.json](examples/mmio_read_contract.json) for the optional explicit-offset read-only probe format. The MMIO example records this project's observed image-processing IP; revalidate its values against the exact Overlay before reusing it.
