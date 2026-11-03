"""Restore the previously verified USB3320 Host device-tree overlay.

No PL bitstream, MIO register, camera setting, or motor register is changed.
The overlay is applied once per boot and is safe to call repeatedly.
"""
import argparse
import json
import time
from pathlib import Path


def restore(overlay_file, wait_seconds=8):
    source = Path(overlay_file)
    overlay = Path('/sys/kernel/config/device-tree/overlays/usb-camera-host')
    device = Path('/sys/bus/platform/devices/e0002000.usb')
    driver = Path('/sys/bus/platform/drivers/chipidea-usb2')
    if not source.is_file():
        raise RuntimeError('verified USB Host overlay is missing')
    applied = False
    if not (device/'driver').exists():
        if not overlay.exists():
            overlay.mkdir()
            (overlay/'dtbo').write_bytes(source.read_bytes())
            applied = True
            time.sleep(1)
        elif (overlay/'status').read_text().strip() != 'applied':
            raise RuntimeError('existing USB overlay is not applied; inspect before retrying')
        if not (device/'driver').exists():
            (driver/'bind').write_text(device.name)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        nodes = sorted(str(p) for p in Path('/dev').glob('video*'))
        if nodes:
            break
        time.sleep(.2)
    devices = []
    for p in Path('/sys/bus/usb/devices').glob('*'):
        if (p/'idVendor').exists():
            devices.append({k: (p/k).read_text().strip() if (p/k).exists() else None
                            for k in ('idVendor', 'idProduct', 'product', 'speed')})
    bound = (device/'driver').exists()
    result = {'overlay_applied_now': applied, 'controller_bound': bound,
              'usb_devices': devices, 'video_nodes': nodes,
              'camera_present': any(d['idVendor'] == '1bcf' and d['idProduct'] == '2cb4'
                                    for d in devices),
              'camera_capture_verified': False}
    print('USB_HOST_STATUS '+json.dumps(result, sort_keys=True), flush=True)
    if not bound:
        raise RuntimeError('USB Host controller did not bind')
    # An unplugged optional camera must not prevent OV5640 startup.
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--overlay', required=True)
    parser.add_argument('--wait-seconds', type=float, default=8)
    args = parser.parse_args()
    restore(args.overlay, args.wait_seconds)
