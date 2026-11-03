"""Keep subprocess descendants inside an application-owned Windows job object."""
import os


class ProcessTree:
    def __init__(self, proc):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_ = [('PerProcessUserTimeLimit', c.c_longlong), ('PerJobUserTimeLimit', c.c_longlong),
                        ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', c.c_size_t),
                        ('MaximumWorkingSetSize', c.c_size_t), ('ActiveProcessLimit', w.DWORD),
                        ('Affinity', c.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(n, c.c_ulonglong) for n in ('ReadOperationCount', 'WriteOperationCount',
                'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class Extended(c.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO), ('ProcessMemoryLimit', c.c_size_t),
                        ('JobMemoryLimit', c.c_size_t), ('PeakProcessMemoryUsed', c.c_size_t),
                        ('PeakJobMemoryUsed', c.c_size_t)]
        k = c.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        k.CreateJobObjectW.restype = w.HANDLE
        k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        k.CloseHandle.argtypes = [w.HANDLE]
        h = k.CreateJobObjectW(None, None)
        if not h:
            raise c.WinError(c.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if (not k.SetInformationJobObject(h, 9, c.byref(info), c.sizeof(info)) or
                not k.AssignProcessToJobObject(h, w.HANDLE(int(proc._handle)))):
            err = c.get_last_error()
            k.CloseHandle(h)
            raise c.WinError(err)
        self.handle, self.kernel = h, k

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
