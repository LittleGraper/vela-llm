"""Tie Windows workspace children to their owner without relying on exit callbacks."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from uuid import uuid4

WORKSPACE_JOB_ENV = "VELA_LLM_WORKSPACE_JOB"
_EXTENDED_LIMIT_INFORMATION = 9
_KILL_ON_JOB_CLOSE = 0x2000


def command_python(env: dict[str, str]) -> str:
    """Use CPython's multiprocessing strategy to bypass the Windows venv redirector."""
    base = getattr(sys, "_base_executable", sys.executable)
    if sys.platform == "win32" and os.path.normcase(base) != os.path.normcase(sys.executable):
        # Preserve the venv's packages and sys.executable without an extra launcher
        # and its independent job, which would kill background descendants on exit.
        env["__PYVENV_LAUNCHER__"] = sys.executable
        return base
    return sys.executable


class _BasicLimits(ctypes.Structure):
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


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    declarations = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "OpenJobObjectW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "QueryInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        "GetCurrentProcess": ([], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in declarations.items():
        function = getattr(kernel, name)
        function.argtypes = arguments
        function.restype = result
    return kernel


class WorkspaceJob:
    """Only the workspace owns a lasting handle; losing it kills its process tree."""

    def __init__(self) -> None:
        self.name = f"Local\\VELA-workspace-{uuid4()}"
        self.kernel = _kernel32()
        # No SECURITY_ATTRIBUTES means the handle cannot be inherited by children.
        self.handle = self.kernel.CreateJobObjectW(None, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self._set_limits(_KILL_ON_JOB_CLOSE)
        except OSError:
            self.close()
            raise

    def _set_limits(self, flags: int) -> None:
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = flags
        if not self.kernel.SetInformationJobObject(
            self.handle, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def release(self) -> None:
        """An explicitly confirmed exit lets the background proxy keep running."""
        if self.handle:
            self._set_limits(0)
            self.close()

    def is_empty(self) -> bool:
        if not self.handle:
            return True
        accounting = _Accounting()
        if not self.kernel.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return accounting.ActiveProcesses == 0

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class WorkspaceJobs:
    """Track each command tree separately so unfinished commands can be cancelled."""

    def __init__(self) -> None:
        self.jobs: list[WorkspaceJob] = []
        self.current: WorkspaceJob | None = None

    def new_command(self) -> str:
        self.prune()
        job = WorkspaceJob()
        self.jobs.append(job)
        self.current = job
        return job.name

    def finish_command(self) -> None:
        self.current = None
        self.prune()

    def prune(self) -> None:
        for job in self.jobs[:]:
            if job.is_empty():
                job.close()
                self.jobs.remove(job)

    def release(self) -> None:
        # Cancel an unfinished command tree, including any Windows venv redirector
        # descendants; only successfully detached background work survives exit.
        if self.current is not None:
            self.current.close()
            self.jobs.remove(self.current)
            self.current = None
        for job in self.jobs:
            job.release()
        self.jobs.clear()

    def close(self) -> None:
        for job in self.jobs:
            job.close()
        self.jobs.clear()
        self.current = None


def join_workspace_job() -> None:
    """Join before executing a command; any descendants inherit job membership."""
    name = os.environ.pop(WORKSPACE_JOB_ENV, None)
    if sys.platform != "win32" or not name:
        return
    kernel = _kernel32()
    handle = kernel.OpenJobObjectW(0x0001, False, name)  # JOB_OBJECT_ASSIGN_PROCESS
    if not handle:
        # If the workspace died before launch completed, do not run an orphan command.
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)
