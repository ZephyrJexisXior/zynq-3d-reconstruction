"""Import pure control helpers while rejecting every attempt at board access."""
import sys
import types


def block_hardware():
    def unavailable(*args, **kwargs):
        raise RuntimeError('Hardware access is forbidden in the offline test suite')
    module = types.ModuleType('pynq')
    module.Clocks = module.MMIO = module.Overlay = module.allocate = unavailable
    sys.modules['pynq'] = module
