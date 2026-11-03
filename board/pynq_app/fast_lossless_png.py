"""Fast, pixel-exact PNG storage for 8-bit BGR stills on the Zynq PS.

Use standard RGB PNG with filter None and DEFLATE stored blocks. This avoids
expensive per-row filter selection and compression on the Cortex-A9. Files
are larger; resolution/color samples and decoder compatibility are retained.
"""
import binascii
import hashlib
from pathlib import Path
import struct
import zlib

import cv2
import numpy as np

STORAGE_PROFILE = 'png_rgb8_filter_none_deflate_level0_verified_v1'


def _chunk(kind, data):
    checksum = binascii.crc32(data, binascii.crc32(kind)) & 0xFFFFFFFF
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', checksum)


def encode_bgr(image):
    if (not isinstance(image, np.ndarray) or image.ndim != 3 or
            image.shape[2] != 3 or image.dtype != np.uint8 or
            min(image.shape[:2]) < 1):
        raise ValueError('PNG storage requires a nonempty uint8 BGR color image')
    height, width = image.shape[:2]
    rows = np.empty((height, 1 + width * 3), dtype=np.uint8)
    rows[:, 0] = 0
    # PNG stores RGB; all pipeline frames use OpenCV BGR. Assignment also
    # handles non-contiguous crops without changing spatial orientation.
    rows[:, 1:].reshape(height, width, 3)[:] = image[:, :, ::-1]
    encoded = zlib.compress(rows.tobytes(), 0)
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header) + _chunk(b'IDAT', encoded) + _chunk(b'IEND', b'')


def save_verified_png(path, image):
    path = Path(path)
    temporary = path.with_suffix('.png.tmp')
    if path.exists() or temporary.exists():
        raise ValueError('refusing to replace an existing photograph or pending write')
    data = encode_bgr(image)
    with temporary.open('xb') as stream:
        stream.write(data)
    stored = temporary.read_bytes()
    decoded = cv2.imdecode(np.frombuffer(stored, np.uint8), cv2.IMREAD_COLOR)
    if stored != data or decoded is None or not np.array_equal(decoded, image):
        raise RuntimeError('saved PNG lossless verification failed')
    temporary.replace(path)
    return hashlib.sha256(stored).hexdigest()
