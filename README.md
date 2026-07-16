# VRAM Monitor

Real-time per-process video memory monitor for Windows.
Shows which processes use how much VRAM (dedicated + shared),
lets you open a process folder and kill it to free memory.

Data comes from Windows performance counters `GPU Process Memory` —
the same numbers Task Manager shows, so **all** processes are visible
(CUDA, games, browsers, DWM), not just compute apps like with `nvidia-smi`.

## Run

```
run_gui.bat        — GUI version (recommended)
run_gui_admin.bat  — GUI as administrator (to kill other/system processes)
run.bat            — console version
run_admin.bat      — console version as administrator
```

Or directly: `python vram_gui.py` / `python vram_monitor.py`

Requires Python 3.7+ only — no third-party libraries (GUI uses tkinter).

## GUI version

- table refreshes every second, overall VRAM usage bar at the top;
- click a column header to sort;
- double-click a row or "Open folder" — process folder in Explorer;
- Del or "Kill process" — terminate the selected process (with confirmation);
- right-click — context menu (open folder, copy path, restart, kill);
- "Restart process" — kills and relaunches the exe; shell processes
  (`dwm.exe`, `explorer.exe`, ...) are restarted by Windows itself;
- "Pause" stops refreshing so the table does not jump under the cursor.

## Console commands

| Command | Action |
|---------|--------|
| `k N` + Enter | Kill process on row N (asks for y/n confirmation) |
| `o N` + Enter | Open process N folder in Explorer (selects the exe) |
| `q` | Quit |

## Flags

```
--interval 0.5    refresh interval in seconds (default 1.0)
--once            print one snapshot and exit (handy for scripts)
```

## Notes

- Processes under `C:\Windows` are dimmed — you usually should not kill them
  (e.g. `dwm.exe` is the desktop compositor; it will restart,
  but the screen will flicker).
- "Access denied" when killing — run via `run_gui_admin.bat`
  (or `run_admin.bat` for the console version). Killing `dwm.exe` and
  reading its exe path REQUIRE admin rights: dwm runs as a separate user
  (`Window Manager\DWM-1`), and the app enables SeDebugPrivilege at startup
  to reach it. The window title shows "Administrator" when elevated.
- Per-process "Dedicated Usage" is *committed* memory, not resident:
  Windows pages inactive surfaces out to system RAM under pressure, so
  the sum of all rows can exceed the actual total used shown at the top.
- The overall VRAM usage bar and GPU name come from `nvidia-smi`;
  without it the app still works, just without total memory capacity.
