# PYNQ and PL/PS Pitfalls

Each item records a trigger, symptom, diagnosis, mitigation, and the observation that motivated it. Check the target design instead of assuming every board behaves like the source project.

| Trigger | Symptom | Diagnosis and mitigation | Derived from |
|---|---|---|---|
| `.bit` and `.hwh` came from different builds or the wrong Overlay was selected | IPs disappear, address map is stale, or the version register differs | Keep artifacts together; compare release hashes; check expected IP names, dimensions, and a design version/capability register before enabling data flow | Source app rejects missing IPs, unsupported Overlay versions, and mismatched dimensions before capture |
| A PS-written buffer is sent to PL without cache flush | PL sees old input samples | Flush the allocated input buffer before starting DMA; use a known pattern and compare output | Existing board path explicitly flushes before DMA; lossless-storage tests check byte and color integrity |
| PS reads a PL-written buffer before invalidate | Output appears stale, partly updated, or repeats a previous frame | Wait for completion, check status, then invalidate before reading; do not reuse a buffer still owned by DMA | Existing VDMA path invalidates completed stores and rejects stale frame/quality counters |
| DMA is polled without a deadline or errors are ignored | Host appears hung or accepts corrupted output | Use bounded polling, capture channel status/error bits, and preserve diagnostic state on timeout | Source controller uses monotonic deadlines and reports VDMA status/error causes |
| Treating IOC as the only completion signal on an interruptless/circular VDMA design | Frame data advances but the expected IOC bit remains clear | Read the configured VDMA/frame-store semantics; verify store index and frame counters instead of assuming IOC routing | Source code comments document store advances while IOC stays clear because that design does not route the VDMA interrupt to PS |
| AXI stream frame length, stride, and memory shape disagree | Early/late SOF/EOL, truncated rows, or shifted pixels | Calculate bytes per line and frame from the contract; verify row count and pixel count in hardware telemetry | Source quality extension compares rows and frame pixels and marks bad frames |
| Treat software observation timestamps as exposure synchronization | Two cameras' frame timestamps look close but images are geometrically inconsistent | Record exposure/trigger synchronization separately; quantify pipeline buffering and rolling-shutter behavior | Existing concurrent-capture tests assert that software observation delta is not hardware/exposure synchronization |
| Use a clock assumption from another Zynq board | PWM/timeout/capture timing scales unexpectedly | Read the board reference clock from trusted metadata and verify programmed FCLK; avoid private PYNQ clock internals unless the PYNQ version and board require a documented workaround | The source board's crystal frequency differed from the PYNQ default assumption and was corrected before capture |
| Assume a USB camera's advertised resolution equals delivered frames | Requested high resolution silently produces smaller frames | Query the negotiated stream size; record actual width/height and reject unexpected dimensions | Source capture work checks actual USB frame dimensions rather than trusting the requested setting |

## Diagnostic order

1. Verify artifact hashes and Overlay identity.
2. Read IP discovery and register/version values before starting DMA.
3. Check DMA status, errors, address alignment, transfer length, and buffer lifetime.
4. Check flush/invalidate order and verify data with a deterministic pattern.
5. Check stream framing, row stride, and actual negotiated dimensions.
6. Only then investigate algorithm output or performance.
