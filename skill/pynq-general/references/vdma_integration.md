# AXI VDMA Integration

AXI VDMA moves video frames through frame stores. It is not interchangeable
with the simple AXI DMA send/receive channels in `tools/dma_helpers.py`.

Before using a VDMA path, record the installed PYNQ version and check which
video/frame-buffer API that release actually provides. Confirm the generated
HWH mapping, S2MM/MM2S directions, frame-store count, pixel format, width,
height, byte stride, buffer depth, circular or parking mode, frame-sync source,
interrupt routing, and the documented completion/error conditions. Derive
these values from the exact Overlay and application, not from a different PYNQ
release or board.

On the observed PYNQ 2.7.0 image, the valid import is
`from pynq.lib.video.dma import AxiVDMA`; importing `AxiVDMA` directly from
`pynq.lib.video` fails. Import availability alone does not prove that the class
can bind the current HWH or operate without routed interrupts. The deployed
application uses its own polling adapter for its VDMA design.

Prefer the installed release's supported video/frame API when it matches the
design. Otherwise use a project-specific adapter with explicit register
definitions and buffer ownership. Do not add a generic wrapper around VDMA
register writes: start/reset sequences, write-one-to-clear status, frame
pointer semantics, and recovery behavior depend on the generated design.

For every transfer path, keep allocated buffers alive while hardware can access
them, synchronize cache according to the buffer allocator, validate stride and
frame size, observe the actual frame-store advance or documented interrupt,
check error status, and stop hardware before releasing buffers. Make the test
bounded and use a known frame/vector where practical. Capture raw status,
timestamps, buffer addresses, and output hashes so a failure can be localized.

The source project's PYNQ 2.7.0 board was observed using AXI VDMA S2MM through
its existing camera application. Its existing capture logs are evidence for
that application path only; they do not establish compatibility of the generic
simple-DMA helper or a newly written VDMA adapter.
