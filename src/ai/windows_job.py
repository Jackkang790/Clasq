"""Minimal Windows Job Object wrapper for owned child-process lifetime.

Only a process handle supplied by the caller is assigned.  There is no process
enumeration or name-based cleanup, so unrelated llama-server instances cannot
be affected.
"""

from __future__ import annotations

import os


class WindowsJobError(OSError):
    pass


class KillOnCloseJob:
    """Kill assigned processes when the owning Clasq process loses this handle."""

    def __init__(self) -> None:
        self._handle = None
        if os.name != "nt":
            return

        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        )
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise WindowsJobError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise WindowsJobError(error, "SetInformationJobObject failed")
        self._handle = handle
        self._kernel32 = kernel32

    def assign(self, process) -> None:
        if os.name != "nt" or self._handle is None:
            return
        import ctypes
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise WindowsJobError("Popen process handle is unavailable")
        if not self._kernel32.AssignProcessToJobObject(self._handle, int(process_handle)):
            raise WindowsJobError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
