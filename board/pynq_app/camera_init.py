"""Initialize the OV5640 over the board's PS EMIO SCCB GPIO pins."""

import time
from contextlib import contextmanager
from pathlib import Path

from pynq import MMIO


GPIO_BASE = 0xE000A000
GPIO_SIZE = 0x1000
# Zynq-7000 GPIO pins 54/55 (the PS EMIO lines) are bank 2.
GPIO_DATA2 = 0x048
GPIO_DATA2_RO = 0x068
GPIO_DIRM2 = 0x284
GPIO_OEN2 = 0x288
SCL = 1 << 0
SDA = 1 << 1
LINES = SCL | SDA
GPIO_POWER_CONTROL = Path('/sys/bus/platform/devices/e000a000.gpio/power/control')
SENSOR_WRITE = 0x78
SENSOR_READ = 0x79

# Register/value pairs from the project's 800x480, RGB565 board profile.
PROFILE = bytes.fromhex("""
3008823008023103023017FF3018FF30371331080136303636310E3632E23633123621E03704A037035A371578
371701370B6037051A39050239061039010A373112360008360133302D60362052371B20471C503A13433A1800
3A19F83635133636033634403622013C01343C04283C05983C06003C07083C08003C091C3C0A9C3C0B40381000
38111038120037086440010240051A3000003004FF430061501F01440E005000A73A0F303A10283A1B303A1E26
3A11603A1F1458002358011458020F58030F58041258052658060C580708580805580905580A08580B0D580C08
580D03580E00580F0058100358110958120758130358140058150158160358170858180D581908581A05581B06
581C08581D0E581E29581F17582011582111582215582328582446582526582608582726582864582926582A24
582B22582C24582D24582E06582F22583040583142583224583326583424583522583622583726583844583924
583A26583B28583C42583DCE5180FF5181F2518200518314518425518524518609518709518809518975518A54
518BE0518CB2518D42518E3D518F565190465191F85192045193705194F05195F0519603519701519804519912
519A04519B00519C06519D82519E3854800154810854821454832854845154856554867154877D548887548991
548A9A548BAA548CB8548DCD548EDD548FEA54901D53811E53825B53830853840A53857E53868853877C53886C
538910538A01538B98558006558340558410558910558A00558BF8501D40530008530130530210530300530408
530530530608530716530908530A30530B04530C0650250030351130365A3C0708382046382101381431381531
38000038010038020038030438040A38053F38060738079B380803380920380A01380BE0380C07380D08380E03
380FE8381306361800361229370952370C033A02173A03103A14173A1510400402471303440704460C22483722
3824025001A33B070A503D00301602301C02301902301900
""")

# OV5640 1080p DVP crop with 1:1 sampling; keep the RGB565 ISP settings above.
HD_OVERRIDES = (
    (0x3035, 0x21),
    (0x3820, 0x46), (0x3821, 0x00),
    (0x3814, 0x11), (0x3815, 0x11),
    (0x3800, 0x01), (0x3801, 0x50),
    (0x3802, 0x01), (0x3803, 0xB2),
    (0x3804, 0x08), (0x3805, 0xEF),
    (0x3806, 0x05), (0x3807, 0xF1),
    (0x3808, 0x07), (0x3809, 0x80),
    (0x380A, 0x04), (0x380B, 0x38),
    (0x380C, 0x09), (0x380D, 0xC4),
    (0x380E, 0x04), (0x380F, 0x60),
    (0x3811, 0x10), (0x3813, 0x04),
    (0x3618, 0x04), (0x3612, 0x2B),
    (0x3708, 0x64), (0x3709, 0x12), (0x370C, 0x00),
    (0x3A02, 0x04), (0x3A03, 0x60),
    (0x3A14, 0x04), (0x3A15, 0x60),
    # The baseline 0x11 system divider overflowed the video-in bridge.
    # The 0x21 divider passed 60 seconds of complete 1080p DMA capture.
    (0x4004, 0x06), (0x460C, 0x20), (0x3824, 0x01),
)

# OV5640 720p DVP window and clock, retaining the board's RGB565 ISP profile.
HD_720_OVERRIDES = (
    (0x3035, 0x21), (0x3036, 0x46),
    (0x3820, 0x41), (0x3821, 0x07),
    (0x3814, 0x31), (0x3815, 0x31),
    (0x3800, 0x00), (0x3801, 0x00),
    (0x3802, 0x00), (0x3803, 0xFA),
    (0x3804, 0x0A), (0x3805, 0x3F),
    (0x3806, 0x06), (0x3807, 0xA9),
    (0x3808, 0x05), (0x3809, 0x00),
    (0x380A, 0x02), (0x380B, 0xD0),
    (0x380C, 0x07), (0x380D, 0x64),
    (0x380E, 0x02), (0x380F, 0xE4),
    (0x3811, 0x10), (0x3813, 0x04),
    (0x3618, 0x00), (0x3612, 0x29),
    (0x3708, 0x64), (0x3709, 0x52), (0x370C, 0x03),
    (0x3A02, 0x02), (0x3A03, 0xE4),
    (0x3A14, 0x02), (0x3A15, 0xE4),
    (0x4004, 0x02), (0x460C, 0x22), (0x3824, 0x02),
)

# OV5640 full active array. Keep the validated HD PLL/divider and use full-
# frame blanking to lower the frame rate while retaining the board clock.
FULL_5MP_OVERRIDES = (
    (0x3035, 0x21),
    (0x3820, 0x46), (0x3821, 0x00),
    (0x3814, 0x11), (0x3815, 0x11),
    (0x3800, 0x00), (0x3801, 0x00),
    (0x3802, 0x00), (0x3803, 0x00),
    (0x3804, 0x0A), (0x3805, 0x3F),
    (0x3806, 0x07), (0x3807, 0x9F),
    (0x3808, 0x0A), (0x3809, 0x20),
    (0x380A, 0x07), (0x380B, 0x98),
    (0x380C, 0x0B), (0x380D, 0x1C),
    (0x380E, 0x07), (0x380F, 0xB0),
    (0x3811, 0x10), (0x3813, 0x06),
    # 1:1 full-array output needs the non-binned analogue readout too.
    # The VGA/720p values (00/29/52/03) produced a black/green four-row
    # pattern despite a valid 2592x1944 frame. Sensor color bars bypassed
    # the fault; these full-readout values remove it before the PL ISP.
    (0x3618, 0x04), (0x3612, 0x2B),
    (0x3708, 0x64), (0x3709, 0x12), (0x370C, 0x00),
    (0x3A02, 0x07), (0x3A03, 0xB0),
    (0x3A14, 0x07), (0x3A15, 0xB0),
    # Full-array 1:1 DVP output must bypass the VGA ISP scaler. Leaving
    # its enable bit set produced 1933 HREF lines / 1930 AXI lines on board.
    (0x5001, 0x83),
    (0x4004, 0x06), (0x460C, 0x20), (0x3824, 0x01),
)


class Sccb:
    def __init__(self):
        # GPIO 54/55 are EMIO[0:1], bank 2 bits 0/1. Linux runtime PM
        # gates this controller's clock when no kernel GPIO consumer exists.
        # MMIO alone does not wake it; suspended registers read back as zero.
        self._power = GPIO_POWER_CONTROL
        self._previous_power = self._power.read_text().strip()
        self._closed = False
        try:
            self._power.write_text('on')
            # Runtime PM resumes the GPIO controller asynchronously.  Wait
            # until its registers are live before the first SCCB transaction.
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                if self._power.with_name('runtime_status').read_text().strip() == 'active':
                    break
                time.sleep(0.01)
            self.gpio = MMIO(GPIO_BASE, GPIO_SIZE)
            self._drive(LINES)
            self.gpio.write(GPIO_DIRM2, self.gpio.read(GPIO_DIRM2) | LINES)
            self.gpio.write(GPIO_OEN2, self.gpio.read(GPIO_OEN2) | LINES)
            if (self.gpio.read(GPIO_DIRM2) & LINES != LINES or
                    self.gpio.read(GPIO_OEN2) & LINES != LINES or
                    self.gpio.read(GPIO_DATA2) & LINES != LINES):
                raise RuntimeError('PS GPIO bank 2 register write failed')
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self._closed:
            self._power.write_text(self._previous_power)
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def _delay(self):
        time.sleep(0.000002)

    def _drive(self, value):
        data = self.gpio.read(GPIO_DATA2)
        self.gpio.write(GPIO_DATA2, (data & ~LINES) | (value & LINES))

    def _sda_input(self, enabled):
        direction = self.gpio.read(GPIO_DIRM2)
        output_enable = self.gpio.read(GPIO_OEN2)
        if enabled:
            self.gpio.write(GPIO_DIRM2, direction & ~SDA)
            self.gpio.write(GPIO_OEN2, output_enable & ~SDA)
        else:
            self.gpio.write(GPIO_DIRM2, direction | SDA)
            self.gpio.write(GPIO_OEN2, output_enable | SDA)

    def _read_sda(self):
        return bool(self.gpio.read(GPIO_DATA2_RO) & SDA)

    def start(self):
        self._sda_input(False)
        self._drive(LINES)
        self._delay()
        self._drive(SCL)
        self._delay()
        self._drive(0)
        self._delay()
        self._drive(0)

    def stop(self):
        self._sda_input(False)
        self._drive(0)
        self._delay()
        self._drive(SCL)
        self._delay()
        self._drive(LINES)
        self._delay()

    def write_byte(self, value):
        for bit in range(7, -1, -1):
            self._drive(0)
            if value & (1 << bit):
                self._drive(SDA)
            self._delay()
            self._drive((SCL | (SDA if value & (1 << bit) else 0)))
            self._delay()
            self._drive(SDA if value & (1 << bit) else 0)
        self._sda_input(True)
        self._drive(0)
        self._delay()
        self._drive(SCL)
        self._delay()
        acknowledged = not self._read_sda()
        self._drive(0)
        self._sda_input(False)
        return acknowledged

    def read_byte(self):
        value = 0
        self._sda_input(True)
        for _ in range(8):
            self._drive(0)
            self._delay()
            self._drive(SCL)
            self._delay()
            value = (value << 1) | int(self._read_sda())
        self._drive(0)
        self._sda_input(False)
        self._drive(SDA)
        self._drive(SCL | SDA)
        self._delay()
        self._drive(SDA)
        return value

    def write_register(self, address, value):
        self.start()
        for byte in (SENSOR_WRITE, address >> 8, address & 0xFF, value):
            if not self.write_byte(byte):
                self.stop()
                raise RuntimeError("OV5640 SCCB NACK at register 0x%04X" % address)
        self.stop()

    def read_register(self, address):
        self.start()
        for byte in (SENSOR_WRITE, address >> 8, address & 0xFF):
            if not self.write_byte(byte):
                self.stop()
                raise RuntimeError("OV5640 SCCB NACK at register 0x%04X" % address)
        self.stop()
        self.start()
        if not self.write_byte(SENSOR_READ):
            self.stop()
            raise RuntimeError("OV5640 SCCB read NACK")
        value = self.read_byte()
        self.stop()
        return value


def initialize_ov5640(resolution="800x480"):
    if resolution not in ("800x480", "1280x720", "1920x1080", "2592x1944"):
        raise ValueError("unsupported OV5640 resolution")
    if len(PROFILE) % 3:
        raise RuntimeError("invalid OV5640 register profile")
    with Sccb() as sccb:
        time.sleep(0.020)
        sensor_id = (sccb.read_register(0x300A) << 8) | sccb.read_register(0x300B)
        if sensor_id != 0x5640:
            raise RuntimeError("OV5640 not detected: ID=0x%04X" % sensor_id)
        for offset in range(0, len(PROFILE), 3):
            address = int.from_bytes(PROFILE[offset:offset + 2], "big")
            value = PROFILE[offset + 2]
            sccb.write_register(address, value)
            if address == 0x3008 and value == 0x82:
                time.sleep(0.001)
            elif address == 0x3008 and value == 0x02:
                # Keep SCCB/PLL running while suppressing DVP pixels. The
                # bridge must not learn transient geometry during setup.
                sccb.write_register(0x4202, 0x0F)
        if resolution != "800x480":
            overrides = {
                "1280x720": HD_720_OVERRIDES,
                "1920x1080": HD_OVERRIDES,
                "2592x1944": FULL_5MP_OVERRIDES,
            }[resolution]
            for address, value in overrides:
                sccb.write_register(address, value)
            expected_geometry = {
                "1280x720": (0x05, 0x00, 0x02, 0xD0),
                "1920x1080": (0x07, 0x80, 0x04, 0x38),
                "2592x1944": (0x0A, 0x20, 0x07, 0x98),
            }[resolution]
            for address, expected in zip(
                    (0x3808, 0x3809, 0x380A, 0x380B), expected_geometry):
                if sccb.read_register(address) != expected:
                    raise RuntimeError("OV5640 HD geometry did not read back")
            if resolution == "2592x1944":
                for address, expected in ((0x5001, 0x83), (0x3618, 0x04),
                                          (0x3612, 0x2B), (0x3709, 0x12), (0x370C, 0x00)):
                    if sccb.read_register(address) != expected:
                        raise RuntimeError("OV5640 5MP readout register 0x%04X did not read back" % address)
        sccb.write_register(0x4202, 0x00)
        return sensor_id


@contextmanager
def keep_gpio_power_active():
    """Keep the EMIO SCCB GPIO clocked while the sensor is streaming."""
    previous = GPIO_POWER_CONTROL.read_text().strip()
    GPIO_POWER_CONTROL.write_text('on')
    try:
        yield
    finally:
        GPIO_POWER_CONTROL.write_text(previous)
