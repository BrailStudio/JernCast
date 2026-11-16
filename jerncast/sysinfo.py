"""CPU use and memory use for the status bar (no extra packages)."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

IS_WINDOWS = os.name == "nt"


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    @property
    def value(self) -> int:
        return (self.high << 32) | self.low


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def _cpu_times() -> tuple[int, int]:
    """(busy, total) counters for the whole machine."""
    if IS_WINDOWS:
        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        total = kernel.value + user.value                 # kernel time includes idle time
        return total - idle.value, total
    with open("/proc/stat", encoding="utf-8") as fh:
        parts = [int(x) for x in fh.readline().split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts[:8])
    return total - idle, total


class CpuMeter:
    """percent() gives the machine's CPU use since the previous call."""

    def __init__(self):
        self._last = None
        try:
            self._last = _cpu_times()
        except Exception:
            pass

    def percent(self) -> float:
        try:
            now = _cpu_times()
        except Exception:
            return 0.0
        last, self._last = self._last, now
        if last is None or now[1] <= last[1]:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (now[0] - last[0]) / (now[1] - last[1])))


def process_memory_mb(pids) -> float:
    """Memory in use by these processes, in megabytes (0 for ones that are gone)."""
    total = 0.0
    if IS_WINDOWS:
        kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
        for pid in pids:
            handle = kernel32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                continue
            counters = _MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                total += counters.WorkingSetSize / (1024 * 1024)
            kernel32.CloseHandle(handle)
        return total
    for pid in pids:
        try:
            with open(f"/proc/{int(pid)}/status", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS"):
                        total += int(line.split()[1]) / 1024
        except OSError:
            pass
    return total
