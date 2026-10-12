"""Read-only access to a running game's memory (Windows).

Used the same way the Kingdom Hearts randomizer/speedrun tools use LuaBackend:
addresses are offsets from the game executable's base address. We only ever
*read*; nothing is written to the game.
"""

from __future__ import annotations

import ctypes
import logging
import struct
import sys
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

TYPES = {
    "u8": "<B", "i8": "<b", "u16": "<H", "i16": "<h", "u32": "<I", "i32": "<i",
    "u64": "<Q", "i64": "<q", "f32": "<f", "f64": "<d",
}


def type_size(kind: str) -> int:
    return struct.calcsize(TYPES[kind])


class MemoryError_(OSError):
    pass


class Memory:
    """Interface: ``base``, ``size`` (main module image) and ``read(address, n)`` (absolute)."""

    base = 0
    size = 0
    name = ""

    def read(self, address: int, n: int) -> bytes:
        raise NotImplementedError

    def read_value(self, offset: int, kind: str):
        """Read a value at ``offset`` from the module base."""
        data = self.read(self.base + offset, type_size(kind))
        return struct.unpack(TYPES[kind], data)[0]

    def regions(self, module_only: bool = True) -> list[tuple[int, int]]:
        """Readable (start, size) ranges, absolute addresses."""
        return [(self.base, self.size)]

    def alive(self) -> bool:
        return True

    def close(self) -> None:
        pass


class BytesMemory(Memory):
    """In-memory stand-in used by tests and the scanner unit tests."""

    def __init__(self, data: bytearray, base: int = 0x140000000, name: str = "fake.exe"):
        self.data = data
        self.base = base
        self.size = len(data)
        self.name = name

    def read(self, address: int, n: int) -> bytes:
        off = address - self.base
        if off < 0 or off + n > len(self.data):
            raise MemoryError_("out of range")
        return bytes(self.data[off:off + n])


# ----------------------------------------------------------------- Windows

if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400
    TH32CS_SNAPMODULE = 0x00000008
    TH32CS_SNAPMODULE32 = 0x00000010
    MEM_COMMIT = 0x1000
    PAGE_NOACCESS = 0x01
    PAGE_GUARD = 0x100
    STILL_ACTIVE = 259

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("GlblcntUsage", wintypes.DWORD),
            ("ProccntUsage", wintypes.DWORD), ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
            ("modBaseSize", wintypes.DWORD), ("hModule", wintypes.HMODULE),
            ("szModule", ctypes.c_wchar * 256), ("szExePath", ctypes.c_wchar * 260),
        ]

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
            ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD),
        ]

    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    _k32.ReadProcessMemory.restype = wintypes.BOOL
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    _k32.Module32FirstW.restype = wintypes.BOOL
    _k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                    ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
    _k32.VirtualQueryEx.restype = ctypes.c_size_t
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]


class ProcessMemory(Memory):
    """Opens a running process (read-only) and locates its main module."""

    def __init__(self, pid: int, name: str = ""):
        if sys.platform != "win32":
            raise MemoryError_("reading game memory is only supported on Windows")
        self.pid = pid
        self.name = name
        self.handle = _k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not self.handle:
            raise MemoryError_("could not open process %d (error %d)" % (pid, ctypes.get_last_error()))
        self.base, self.size = self._main_module()

    def _main_module(self) -> tuple[int, int]:
        snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
        if snap in (None, wintypes.HANDLE(-1).value):
            raise MemoryError_("module snapshot failed (error %d)" % ctypes.get_last_error())
        try:
            entry = MODULEENTRY32W()
            entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
            if not _k32.Module32FirstW(snap, ctypes.byref(entry)):
                raise MemoryError_("no modules (error %d)" % ctypes.get_last_error())
            base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
            return base, entry.modBaseSize
        finally:
            _k32.CloseHandle(snap)

    def read(self, address: int, n: int) -> bytes:
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not _k32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buf, n, ctypes.byref(got)) \
                or got.value != n:
            raise MemoryError_("read failed at 0x%X" % address)
        return buf.raw

    def regions(self, module_only: bool = True) -> list[tuple[int, int]]:
        out = []
        start = self.base if module_only else 0
        end = self.base + self.size if module_only else 0x7FFFFFFFFFFF
        addr = start
        mbi = MEMORY_BASIC_INFORMATION()
        while addr < end:
            if not _k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            region_start = mbi.BaseAddress or 0
            region_end = region_start + mbi.RegionSize
            readable = (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD)
                        and not (mbi.Protect & PAGE_NOACCESS) and mbi.Protect != 0)
            if readable:
                s, e = max(region_start, start), min(region_end, end)
                if e > s:
                    out.append((s, e - s))
            if region_end <= addr:
                break
            addr = region_end
        return out

    def alive(self) -> bool:
        code = wintypes.DWORD()
        return bool(_k32.GetExitCodeProcess(self.handle, ctypes.byref(code))) and code.value == STILL_ACTIVE

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None


def open_process(names: list[str]) -> ProcessMemory | None:
    """Attach to the first running process whose name matches one of ``names``."""
    try:
        import psutil
    except ImportError:
        return None
    wanted = {n.lower() for n in names}
    for proc in psutil.process_iter(["pid", "name"]):
        name = proc.info.get("name") or ""
        if name.lower() in wanted:
            try:
                return ProcessMemory(proc.info["pid"], name)
            except MemoryError_ as exc:
                log.warning("Can't read %s: %s", name, exc)
    return None


@dataclass
class Field:
    name: str
    offset: int
    kind: str = "u32"
    pointer: list[int] | None = None  # optional pointer chain: read u64 at offset, then add each step

    def read(self, mem: Memory):
        if not self.pointer:
            return mem.read_value(self.offset, self.kind)
        addr = struct.unpack("<Q", mem.read(mem.base + self.offset, 8))[0]
        for step in self.pointer[:-1]:
            addr = struct.unpack("<Q", mem.read(addr + step, 8))[0]
        data = mem.read(addr + self.pointer[-1], type_size(self.kind))
        return struct.unpack(TYPES[self.kind], data)[0]
