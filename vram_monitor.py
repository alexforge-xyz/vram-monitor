#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VRAM Monitor — real-time per-process video memory monitor (Windows).

Reads Windows performance counters "GPU Process Memory" (the same data as
Task Manager), so it sees ALL processes that use VRAM — both CUDA/compute
and regular graphics apps (browsers, games, DWM, etc.).

No external dependencies — standard library only (ctypes + PDH API).

In-app commands:
    k N   — kill the process on row N (with confirmation)
    o N   — open the process folder for row N in Explorer
    q     — quit

Flags:
    --interval SEC   refresh interval (default 1.0)
    --once           print a single snapshot and exit (non-interactive)
"""

import argparse
import ctypes
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

# ---------------------------------------------------------------------------
# PDH (Performance Data Helper) via ctypes
# ---------------------------------------------------------------------------

pdh = ctypes.WinDLL("pdh")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PDH_FMT_LARGE = 0x00000400
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x00000000
PDH_CSTATUS_NEW_DATA = 0x00000001


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    class _Value(ctypes.Union):
        _fields_ = [
            ("longValue", wintypes.LONG),
            ("doubleValue", ctypes.c_double),
            ("largeValue", ctypes.c_longlong),
            ("AnsiStringValue", ctypes.c_char_p),
            ("WideStringValue", ctypes.c_wchar_p),
        ]

    _anonymous_ = ("u",)
    _fields_ = [("CStatus", wintypes.DWORD), ("u", _Value)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [
        ("szName", wintypes.LPWSTR),
        ("FmtValue", PDH_FMT_COUNTERVALUE),
    ]


pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t,
                              ctypes.POINTER(wintypes.HANDLE)]
pdh.PdhOpenQueryW.restype = ctypes.c_uint32
pdh.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR,
                                      ctypes.c_size_t,
                                      ctypes.POINTER(wintypes.HANDLE)]
pdh.PdhAddEnglishCounterW.restype = ctypes.c_uint32
pdh.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
pdh.PdhCollectQueryData.restype = ctypes.c_uint32
pdh.PdhGetFormattedCounterArrayW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD),
                                             ctypes.POINTER(wintypes.DWORD),
                                             ctypes.c_void_p]
pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_uint32


class GpuCounters:
    """Wrapper around a PDH query with GPU memory counters."""

    PATHS = {
        "proc_dedicated": r"\GPU Process Memory(*)\Dedicated Usage",
        "proc_shared":    r"\GPU Process Memory(*)\Shared Usage",
        "adapter_dedicated": r"\GPU Adapter Memory(*)\Dedicated Usage",
    }

    def __init__(self):
        self.query = wintypes.HANDLE()
        status = pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query))
        if status != 0:
            raise OSError(f"PdhOpenQueryW failed: 0x{status:08X}")
        self.counters = {}
        for key, path in self.PATHS.items():
            hc = wintypes.HANDLE()
            status = pdh.PdhAddEnglishCounterW(self.query, path, 0,
                                               ctypes.byref(hc))
            if status == 0:
                self.counters[key] = hc
        if "proc_dedicated" not in self.counters:
            raise OSError(
                "The 'GPU Process Memory' counter is unavailable. "
                "It requires Windows 10 1709+ / Windows 11 with a WDDM 2.x driver."
            )

    def collect(self):
        pdh.PdhCollectQueryData(self.query)

    def read_array(self, key):
        """Return a list of (instance_name, value) for the given counter key."""
        hc = self.counters.get(key)
        if not hc:
            return []
        size = wintypes.DWORD(0)
        count = wintypes.DWORD(0)
        status = pdh.PdhGetFormattedCounterArrayW(
            hc, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None)
        while status == PDH_MORE_DATA:
            buf = ctypes.create_string_buffer(size.value)
            status = pdh.PdhGetFormattedCounterArrayW(
                hc, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buf)
            if status == 0:
                items = ctypes.cast(buf,
                                    ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W))
                result = []
                for i in range(count.value):
                    item = items[i]
                    if item.FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA,
                                                 PDH_CSTATUS_NEW_DATA):
                        result.append((item.szName, item.FmtValue.largeValue))
                return result
        return []


PID_RE = re.compile(r"pid_(\d+)_")


def snapshot(counters):
    """Build {pid: {'dedicated': bytes, 'shared': bytes}} and total used."""
    counters.collect()
    procs = {}
    for key in ("proc_dedicated", "proc_shared"):
        field = "dedicated" if key == "proc_dedicated" else "shared"
        for name, value in counters.read_array(key):
            m = PID_RE.search(name or "")
            if not m or value <= 0:
                continue
            pid = int(m.group(1))
            entry = procs.setdefault(pid, {"dedicated": 0, "shared": 0})
            entry[field] += value
    total_used = sum(v for _, v in counters.read_array("adapter_dedicated"))
    return procs, total_used


# ---------------------------------------------------------------------------
# Process info / process control (WinAPI)
# ---------------------------------------------------------------------------

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def process_names():
    """{pid: exe name} for all processes — works for protected ones too."""
    names = {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return names
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                names[entry.th32ProcessID] = entry.szExeFile
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return names


_path_cache = {}


def process_path(pid):
    """Full path to the process exe (or None)."""
    if pid in _path_cache:
        return _path_cache[pid]
    path = None
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                   ctypes.byref(size)):
                path = buf.value
        finally:
            kernel32.CloseHandle(h)
    _path_cache[pid] = path
    return path


def kill_process(pid):
    """Terminate a process. Returns (ok, message)."""
    h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        err = ctypes.get_last_error()
        if err == 5:
            return False, "Access denied — run run_admin.bat"
        return False, f"OpenProcess: error {err} (process already exited?)"
    try:
        if kernel32.TerminateProcess(h, 1):
            return True, "Process terminated, VRAM will free in a few seconds"
        return False, f"TerminateProcess: error {ctypes.get_last_error()}"
    finally:
        kernel32.CloseHandle(h)


def open_folder(pid):
    path = process_path(pid)
    if path and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", path])
        return True, f"Opened folder: {os.path.dirname(path)}"
    return False, "Exe path unavailable (system process?)"


def gpu_info():
    """(GPU name, total VRAM in bytes) via nvidia-smi, if available."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            name, total_mib = out.stdout.strip().splitlines()[0].rsplit(",", 1)
            return name.strip(), int(total_mib) * 1024 * 1024
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None, None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"


def enable_ansi():
    handle = kernel32.GetStdHandle(-11)
    mode = wintypes.DWORD()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)


def fmt_bytes(n):
    if n >= 1024 ** 3:
        return f"{n / 1024**3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024**2:.0f} MB"
    return f"{n / 1024:.0f} KB"


def usage_bar(used, total, width=30):
    if not total:
        return ""
    frac = min(used / total, 1.0)
    filled = int(frac * width)
    color = GREEN if frac < 0.7 else (YELLOW if frac < 0.9 else RED)
    return f"{color}{'█' * filled}{DIM}{'░' * (width - filled)}{RESET}"


def build_rows(procs):
    rows = []
    names = process_names()
    for pid, mem in sorted(procs.items(),
                           key=lambda kv: kv[1]["dedicated"], reverse=True):
        path = process_path(pid)
        name = (os.path.basename(path) if path
                else names.get(pid, f"<pid {pid}>"))
        folder = os.path.dirname(path) if path else "—"
        rows.append({"pid": pid, "name": name, "folder": folder,
                     "dedicated": mem["dedicated"], "shared": mem["shared"]})
    return rows


def render(rows, total_used, gpu_name, gpu_total, input_buf, message,
           pending_kill, interval):
    lines = []
    title = f"{BOLD}{CYAN} VRAM Monitor{RESET}"
    if gpu_name:
        title += f"  {DIM}|{RESET}  {gpu_name}"
    lines.append(title)

    if gpu_total:
        lines.append(f" VRAM: {BOLD}{fmt_bytes(total_used)}{RESET} / "
                     f"{fmt_bytes(gpu_total)}  {usage_bar(total_used, gpu_total)}")
    else:
        lines.append(f" VRAM used: {BOLD}{fmt_bytes(total_used)}{RESET}")

    lines.append(f" {DIM}Refreshing every {interval:g} s — "
                 f"{time.strftime('%H:%M:%S')}{RESET}")
    lines.append("")
    lines.append(f"{BOLD}  #   PID      DEDICATED    SHARED     PROCESS"
                 f"                        FOLDER{RESET}")
    lines.append(f" {DIM}{'─' * 100}{RESET}")

    if not rows:
        lines.append(f"  {DIM}(no processes with video memory found){RESET}")
    for i, r in enumerate(rows, 1):
        is_sys = r["folder"].lower().startswith(
            os.environ.get("SystemRoot", r"C:\Windows").lower())
        name_color = DIM if is_sys else ""
        lines.append(
            f"  {i:<3} {r['pid']:<8} {fmt_bytes(r['dedicated']):>10}   "
            f"{fmt_bytes(r['shared']):>8}   {name_color}{r['name']:<30}{RESET} "
            f"{DIM}{r['folder'][:45]}{RESET}")

    lines.append("")
    lines.append(f" {DIM}Commands:{RESET} {BOLD}k N{RESET} — kill process N, "
                 f"{BOLD}o N{RESET} — open folder N, {BOLD}q{RESET} — quit")
    if message:
        lines.append(f" {message}")
    if pending_kill:
        lines.append(f" {YELLOW}{BOLD}Kill {pending_kill[1]} "
                     f"(PID {pending_kill[0]})? [y/n]{RESET}")
    else:
        lines.append(f" > {input_buf}\x1b[K")

    sys.stdout.write("\x1b[H" + "\n".join(lines) + "\x1b[J")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Per-process VRAM monitor")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="refresh interval in seconds (default 1.0)")
    parser.add_argument("--once", action="store_true",
                        help="print one snapshot and exit")
    args = parser.parse_args()

    try:
        counters = GpuCounters()
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    gpu_name, gpu_total = gpu_info()

    if args.once:
        procs, total_used = snapshot(counters)
        rows = build_rows(procs)
        print(f"GPU: {gpu_name or '?'}  |  VRAM used: {fmt_bytes(total_used)}"
              + (f" / {fmt_bytes(gpu_total)}" if gpu_total else ""))
        print(f"{'PID':<8} {'DEDICATED':>10} {'SHARED':>10}  "
              f"{'PROCESS':<30} FOLDER")
        for r in rows:
            print(f"{r['pid']:<8} {fmt_bytes(r['dedicated']):>10} "
                  f"{fmt_bytes(r['shared']):>10}  {r['name']:<30} {r['folder']}")
        return 0

    import msvcrt
    enable_ansi()
    sys.stdout.write("\x1b[?25l\x1b[2J")  # hide cursor, clear screen

    rows, total_used = [], 0
    input_buf = ""
    message = ""
    pending_kill = None  # (pid, name)
    last_refresh = 0.0

    def do_command(cmd):
        nonlocal message, pending_kill
        cmd = cmd.strip().lower()
        if not cmd:
            return
        m = re.fullmatch(r"([ko])\s*(\d+)", cmd)
        if not m:
            message = f"{RED}Unknown command: '{cmd}'{RESET}"
            return
        action, n = m.group(1), int(m.group(2))
        if not (1 <= n <= len(rows)):
            message = f"{RED}No row {n}{RESET}"
            return
        row = rows[n - 1]
        if action == "o":
            ok, msg = open_folder(row["pid"])
            message = (GREEN if ok else RED) + msg + RESET
        else:
            pending_kill = (row["pid"], row["name"])

    try:
        while True:
            now = time.time()
            if now - last_refresh >= args.interval:
                _path_cache.clear()
                procs, total_used = snapshot(counters)
                rows = build_rows(procs)
                last_refresh = now
                render(rows, total_used, gpu_name, gpu_total, input_buf,
                       message, pending_kill, args.interval)

            dirty = False
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):      # special keys — skip
                    msvcrt.getwch()
                    continue
                if pending_kill:
                    pid, name = pending_kill
                    pending_kill = None
                    if ch.lower() in ("y",):
                        ok, msg = kill_process(pid)
                        message = ((GREEN if ok else RED)
                                   + f"{name} (PID {pid}): {msg}" + RESET)
                    else:
                        message = f"{DIM}Cancelled{RESET}"
                    dirty = True
                    continue
                if ch == "\x03" or (ch == "q" and not input_buf):
                    raise KeyboardInterrupt
                if ch == "\r":
                    do_command(input_buf)
                    input_buf = ""
                elif ch == "\x08":
                    input_buf = input_buf[:-1]
                elif ch.isprintable():
                    message = ""
                    input_buf += ch
                dirty = True
            if dirty:
                render(rows, total_used, gpu_name, gpu_total, input_buf,
                       message, pending_kill, args.interval)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\n")  # restore cursor
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
