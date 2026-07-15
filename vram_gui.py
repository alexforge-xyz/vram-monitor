#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VRAM Monitor GUI — graphical per-process video memory monitor (Windows).

Uses the core from vram_monitor.py (Windows "GPU Process Memory" counters).
No external dependencies — standard library only (tkinter).

Controls:
    - click a column header to sort;
    - double-click a row / "Open folder" button — process folder;
    - Del / "Kill process" button — terminate process (with confirmation);
    - right-click — context menu.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

import vram_monitor as core

REFRESH_MS = 1000

# Dark palette
BG = "#1e1e24"
BG_ALT = "#26262e"
FG = "#e8e8ee"
FG_DIM = "#8a8a96"
ACCENT = "#4da3ff"
GREEN = "#4caf50"
YELLOW = "#e6b32e"
RED = "#e05252"

SYSTEM_ROOT = os.environ.get("SystemRoot", r"C:\Windows").lower()

COLUMNS = {
    # id: (title, width, anchor, sort key)
    "pid":       ("PID",        70,  "center", lambda r: r["pid"]),
    "dedicated": ("Dedicated", 100,  "e",      lambda r: r["dedicated"]),
    "shared":    ("Shared",     90,  "e",      lambda r: r["shared"]),
    "name":      ("Process",   220,  "w",      lambda r: r["name"].lower()),
    "folder":    ("Folder",    420,  "w",      lambda r: r["folder"].lower()),
}


class VramMonitorApp:
    def __init__(self, root):
        self.root = root
        self.counters = core.GpuCounters()
        self.gpu_name, self.gpu_total = core.gpu_info()
        self.rows = []
        self.paused = False
        self.sort_col = "dedicated"
        self.sort_desc = True

        root.title("VRAM Monitor")
        root.geometry("1000x560")
        root.minsize(700, 350)
        root.configure(bg=BG)

        self._build_styles()
        self._build_header()
        self._build_table()
        self._build_footer()

        root.bind("<Delete>", lambda e: self.kill_selected())
        root.bind("<F5>", lambda e: self.refresh(force=True))

        self.refresh()

    # ------------------------------------------------------------- layout

    def _build_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG,
                        fieldbackground=BG_ALT, bordercolor=BG_ALT,
                        lightcolor=BG, darkcolor=BG)
        style.configure("Treeview", background=BG_ALT, foreground=FG,
                        fieldbackground=BG_ALT, rowheight=24,
                        font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background="#33333d",
                        foreground=FG, font=("Segoe UI", 10, "bold"),
                        relief="flat")
        style.map("Treeview.Heading", background=[("active", "#3d3d49")])
        style.map("Treeview",
                  background=[("selected", "#31435c")],
                  foreground=[("selected", "#ffffff")])
        style.configure("TButton", background="#33333d", foreground=FG,
                        font=("Segoe UI", 10), padding=(12, 5), relief="flat")
        style.map("TButton", background=[("active", "#3d3d49")])
        style.configure("Kill.TButton", foreground="#ff8a8a")
        for name, color in (("green", GREEN), ("yellow", YELLOW),
                            ("red", RED)):
            style.configure(f"{name}.Horizontal.TProgressbar",
                            background=color, troughcolor=BG_ALT,
                            bordercolor=BG, lightcolor=color, darkcolor=color)

    def _build_header(self):
        frame = tk.Frame(self.root, bg=BG)
        frame.pack(fill="x", padx=12, pady=(10, 6))

        self.gpu_label = tk.Label(
            frame, bg=BG, fg=FG, font=("Segoe UI", 13, "bold"),
            text=self.gpu_name or "GPU")
        self.gpu_label.pack(side="left")

        self.usage_label = tk.Label(frame, bg=BG, fg=FG_DIM,
                                    font=("Segoe UI", 11))
        self.usage_label.pack(side="right")

        self.bar = ttk.Progressbar(
            self.root, style="green.Horizontal.TProgressbar",
            maximum=self.gpu_total or 1)
        self.bar.pack(fill="x", padx=12, pady=(0, 8))

    def _build_table(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=12)

        self.tree = ttk.Treeview(wrap, columns=list(COLUMNS), show="headings",
                                 selectmode="browse")
        for col, (title, width, anchor, _) in COLUMNS.items():
            self.tree.heading(col, text=title,
                              command=lambda c=col: self.set_sort(c))
            self.tree.column(col, width=width, anchor=anchor,
                             stretch=(col == "folder"))
        self.tree.tag_configure("system", foreground=FG_DIM)

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self.open_selected())
        self.tree.bind("<Button-3>", self._popup_menu)

        self.menu = tk.Menu(self.root, tearoff=0, bg=BG_ALT, fg=FG,
                            activebackground="#31435c",
                            activeforeground="#ffffff")
        self.menu.add_command(label="Open folder",
                              command=self.open_selected)
        self.menu.add_command(label="Copy path",
                              command=self.copy_path)
        self.menu.add_separator()
        self.menu.add_command(label="Kill process",
                              command=self.kill_selected)

    def _build_footer(self):
        frame = tk.Frame(self.root, bg=BG)
        frame.pack(fill="x", padx=12, pady=8)

        kill_btn = ttk.Button(frame, text="✕ Kill process",
                              style="Kill.TButton",
                              command=self.kill_selected)
        kill_btn.pack(side="left")
        ttk.Button(frame, text="📂 Open folder",
                   command=self.open_selected).pack(side="left", padx=(8, 0))
        self.pause_btn = ttk.Button(frame, text="⏸ Pause",
                                    command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=(8, 0))

        self.status = tk.Label(frame, bg=BG, fg=FG_DIM,
                               font=("Segoe UI", 10), anchor="e")
        self.status.pack(side="right", fill="x", expand=True)

    # ------------------------------------------------------------- data

    def refresh(self, force=False):
        if not self.paused or force:
            core._path_cache.clear()
            try:
                procs, total_used = core.snapshot(self.counters)
            except OSError as e:
                self.set_status(f"Error reading counters: {e}", RED)
                procs, total_used = {}, 0
            self.rows = core.build_rows(procs)
            self._update_header(total_used)
            self._update_table()
        self.root.after(REFRESH_MS, self.refresh)

    def _update_header(self, total_used):
        if self.gpu_total:
            frac = total_used / self.gpu_total
            self.bar["value"] = total_used
            color = "green" if frac < 0.7 else ("yellow" if frac < 0.9
                                                else "red")
            self.bar.configure(style=f"{color}.Horizontal.TProgressbar")
            self.usage_label.config(
                text=f"VRAM: {core.fmt_bytes(total_used)} / "
                     f"{core.fmt_bytes(self.gpu_total)}  ({frac:.0%})")
        else:
            self.usage_label.config(
                text=f"VRAM used: {core.fmt_bytes(total_used)}")

    def _sorted_rows(self):
        key = COLUMNS[self.sort_col][3]
        return sorted(self.rows, key=key, reverse=self.sort_desc)

    def _update_table(self):
        rows = self._sorted_rows()
        seen = set()
        for i, r in enumerate(rows):
            iid = str(r["pid"])
            seen.add(iid)
            values = (r["pid"], core.fmt_bytes(r["dedicated"]),
                      core.fmt_bytes(r["shared"]), r["name"], r["folder"])
            tags = ("system",) if r["folder"].lower().startswith(
                SYSTEM_ROOT) else ()
            if self.tree.exists(iid):
                self.tree.item(iid, values=values, tags=tags)
                self.tree.move(iid, "", i)
            else:
                self.tree.insert("", i, iid=iid, values=values, tags=tags)
        for iid in self.tree.get_children(""):
            if iid not in seen:
                self.tree.delete(iid)
        for col, (title, *_rest) in COLUMNS.items():
            arrow = ""
            if col == self.sort_col:
                arrow = "  ▼" if self.sort_desc else "  ▲"
            self.tree.heading(col, text=title + arrow)

    def set_sort(self, col):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col = col
            self.sort_desc = col in ("dedicated", "shared")
        self._update_table()

    # ------------------------------------------------------------- actions

    def _selected_row(self):
        sel = self.tree.selection()
        if not sel:
            self.set_status("Select a process in the table first", YELLOW)
            return None
        pid = int(sel[0])
        for r in self.rows:
            if r["pid"] == pid:
                return r
        return None

    def kill_selected(self):
        row = self._selected_row()
        if not row:
            return
        warn = ""
        if row["folder"].lower().startswith(SYSTEM_ROOT):
            warn = "\n\n⚠ This is a Windows system process — killing it " \
                   "is usually a bad idea."
        if not messagebox.askyesno(
                "Kill process",
                f"Terminate {row['name']} (PID {row['pid']})?\n"
                f"VRAM used: {core.fmt_bytes(row['dedicated'])}{warn}",
                icon="warning", parent=self.root):
            return
        ok, msg = core.kill_process(row["pid"])
        self.set_status(f"{row['name']} (PID {row['pid']}): {msg}",
                        GREEN if ok else RED)
        if ok:
            self.refresh(force=True)

    def open_selected(self):
        row = self._selected_row()
        if not row:
            return
        ok, msg = core.open_folder(row["pid"])
        self.set_status(msg, FG_DIM if ok else RED)

    def copy_path(self):
        row = self._selected_row()
        if not row:
            return
        path = core.process_path(row["pid"]) or row["folder"]
        if path and path != "—":
            self.root.clipboard_clear()
            self.root.clipboard_append(path)
            self.set_status(f"Copied: {path}", FG_DIM)
        else:
            self.set_status("Path unavailable", RED)

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.config(text="▶ Resume" if self.paused
                              else "⏸ Pause")
        self.set_status("Paused — data is not updating" if self.paused else "",
                        YELLOW)

    def _popup_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def set_status(self, text, color=FG_DIM):
        self.status.config(text=text, fg=color)


def main():
    root = tk.Tk()
    try:
        # crisp rendering on HiDPI monitors
        ctypes = __import__("ctypes")
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        scale = ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100
        root.tk.call("tk", "scaling", scale * 96 / 72)
    except OSError:
        pass

    try:
        app = VramMonitorApp(root)
    except OSError as e:
        messagebox.showerror("VRAM Monitor", str(e))
        return 1

    if "--smoke" in sys.argv:  # auto-close for smoke test
        root.after(2500, root.destroy)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
