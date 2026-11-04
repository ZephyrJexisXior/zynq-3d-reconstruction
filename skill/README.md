# Reusable Engineering Skills

This directory contains reusable workflows and tools extracted from a Zynq-7000/PYNQ image-processing project. The generic register-access example is hardware-neutral; the read-only MMIO example and observed board record identify one deployed configuration explicitly.

## Skill inventory

| Item | Type | Use it for | Validation status |
|---|---|---|---|
| [pynq-general](pynq-general/README.md) | PYNQ skill, scripts, examples, prompts, pitfalls | Porting an Overlay/DMA/register interface to a different PYNQ design | Offline tests pass; target-board tests are still required for each board and bitstream |

## Quick start

1. Read `pynq-general/README.md` for prerequisites, applicability, failure conditions, and evidence.
2. Use `pynq-general/examples/interface_contract.json` as a hardware-neutral register-contract example.
3. Generate host accessors and a test skeleton:

   ```powershell
   python skill/pynq-general/tools/contract_codegen.py `
     skill/pynq-general/examples/interface_contract.json `
     --output-dir .\generated
   python -m unittest discover -s .\generated -p 'test_*.py'
   ```

4. Run the offline skill tests from the repository root:

   ```powershell
   python -m unittest discover -s skill/pynq-general/tests -p 'test_*.py'
   ```

5. For board use, install a PYNQ image compatible with the target board and adapt the Overlay IP names, expected registers, DMA direction, and buffer shape. Never treat a passing desktop test as proof of physical I/O, timing closure, or cache behavior on silicon.
6. Follow `pynq-general/references/board_validation.md` for board-side checks and archive the completed `pynq-general/examples/board_test_record.json` with raw evidence.

## What makes this reusable

The scripts use the Python standard library and accept explicit paths/contracts. PYNQ is imported only when a board Overlay is actually loaded. Hardware identities and register offsets are inputs, not constants copied from the original project. The project that inspired these materials is cited as evidence only; no runtime code imports it.
