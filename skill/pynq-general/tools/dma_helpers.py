"""Bounded helpers for PYNQ simple AXI DMA channels."""
from __future__ import annotations

import time
from typing import Optional


class DmaTransferError(RuntimeError):
    pass


def _sync(buffer, method: str) -> None:
    operation = getattr(buffer, method, None)
    if not callable(operation):
        raise DmaTransferError("buffer does not support %s()" % method)
    operation()


def _channel_error(channel) -> Optional[str]:
    if bool(getattr(channel, "error", False)):
        return str(getattr(channel, "_error", "DMA channel reports error"))
    return None


def transfer(dma, *, tx_buffer=None, rx_buffer=None, timeout_s=5.0,
             poll_interval_s=0.001) -> dict:
    """Run one simple DMA transfer; RX is armed before TX when both are used.

    Flush is performed before PL reads a PS-produced buffer. Invalidate occurs
    only after the receive channel reports idle, before PS reads PL output.
    The caller owns buffer allocation and must keep buffers alive until return.
    """
    if tx_buffer is None and rx_buffer is None:
        raise ValueError("provide tx_buffer, rx_buffer, or both")
    if timeout_s <= 0 or poll_interval_s <= 0:
        raise ValueError("timeouts and polling interval must be positive")
    channels = []
    if rx_buffer is not None:
        _sync(rx_buffer, "flush")
        dma.recvchannel.transfer(rx_buffer)
        channels.append(("rx", dma.recvchannel))
    if tx_buffer is not None:
        _sync(tx_buffer, "flush")
        dma.sendchannel.transfer(tx_buffer)
        channels.append(("tx", dma.sendchannel))

    deadline = time.monotonic() + timeout_s
    while True:
        errors = {name: _channel_error(channel) for name, channel in channels}
        errors = {name: reason for name, reason in errors.items() if reason}
        if errors:
            raise DmaTransferError("DMA channel error: %s" % errors)
        if all(bool(getattr(channel, "idle", False)) for _, channel in channels):
            break
        if time.monotonic() >= deadline:
            states = {name: {"idle": bool(getattr(channel, "idle", False)),
                             "error": bool(getattr(channel, "error", False))}
                      for name, channel in channels}
            raise TimeoutError("DMA did not become idle within %.3f s: %s" %
                               (timeout_s, states))
        time.sleep(poll_interval_s)

    if rx_buffer is not None:
        _sync(rx_buffer, "invalidate")
    return {"completed": True, "channels": [name for name, _ in channels],
            "cache_actions": (["rx.flush"] if rx_buffer is not None else []) +
                             (["tx.flush"] if tx_buffer is not None else []) +
                             (["rx.invalidate"] if rx_buffer is not None else [])}
