"""The settings window: hub address, start with Windows, and the hotkey list (click a box, press a combo).

Runs its own Tk interpreter on its own thread while it's open; everything from other threads reaches it
through ``self.inbox``.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from . import actions, settings
from .combos import Combo, label as combo_label

HINT = ("Click a box, then press a key combination or a mouse button (side buttons work; left/right click and "
        "the wheel need Ctrl, Alt, Shift or Win with them). Esc cancels.")


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.inbox: queue.Queue = queue.Queue()
        self.recording: str | None = None          # action id being recorded
        self.errors: dict[str, str] = dict(app.binding_errors)
        self.thread = threading.Thread(target=self._run, name="settings-window", daemon=True)

    def show(self):
        if self.thread.is_alive():
            self.inbox.put(("lift",))
        else:
            self.thread.start()

    # -- building -------------------------------------------------------------------------------
    def _run(self):
        self.root = root = tk.Tk()
        root.title("BigScreen — tray settings")
        root.geometry("640x720")
        root.minsize(520, 420)
        try:
            from PIL import ImageTk
            self._icon = ImageTk.PhotoImage(self.app.icon_image(True))
            root.iconphoto(True, self._icon)
        except Exception:
            pass
        base = tkfont.nametofont("TkDefaultFont")
        self.bold = base.copy()
        self.bold.configure(weight="bold")
        self.small = base.copy()
        self.small.configure(size=max(8, base.cget("size") - 1))
        ttk.Style().configure("Section.TLabel", font=self.bold)
        ttk.Style().configure("Error.TLabel", foreground="#c0392b", font=self.small)
        ttk.Style().configure("Faint.TLabel", foreground="#666666", font=self.small)
        ttk.Style().configure("Recording.TButton", foreground="#b35c00")

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        # connection
        conn = ttk.LabelFrame(outer, text="BigScreen", padding=10)
        conn.pack(fill="x")
        ttk.Label(conn, text="Hub address").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.url = tk.StringVar(value=self.app.settings.hub_url)
        e = ttk.Entry(conn, textvariable=self.url)
        e.grid(row=0, column=1, sticky="ew")
        e.bind("<Return>", lambda _e: self._connect())
        ttk.Button(conn, text="Connect", command=self._connect).grid(row=0, column=2, padx=(8, 0))
        self.conn_status = ttk.Label(conn, text=self.app.connection_text(), style="Faint.TLabel")
        self.conn_status.grid(row=1, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self.autostart = tk.BooleanVar(value=settings.autostart_enabled())
        ttk.Checkbutton(conn, text="Start with Windows", variable=self.autostart,
                        command=self._toggle_autostart).grid(row=2, column=1, sticky="w", pady=(8, 0))
        conn.columnconfigure(1, weight=1)

        # hotkeys
        ttk.Label(outer, text="Hotkeys", style="Section.TLabel").pack(anchor="w", pady=(14, 2))
        ttk.Label(outer, text=HINT, style="Faint.TLabel", wraplength=600, justify="left").pack(anchor="w", fill="x")
        box = ttk.Frame(outer, relief="solid", borderwidth=1)
        box.pack(fill="both", expand=True, pady=(8, 0))
        self.canvas = tk.Canvas(box, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.rows = ttk.Frame(self.canvas, padding=(10, 6))
        win = self.canvas.create_window((0, 0), window=self.rows, anchor="nw")
        self.rows.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda ev: self.canvas.itemconfigure(win, width=ev.width))
        root.bind_all("<MouseWheel>", self._wheel)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))
        self.status = ttk.Label(bottom, text="", wraplength=470, justify="left")
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="Close", command=self._close).pack(side="right")

        root.protocol("WM_DELETE_WINDOW", self._close)
        self._fill()
        root.after(50, self._poll)
        root.lift()
        root.focus_force()
        root.mainloop()
        self.root = None

    def _wheel(self, ev):
        if self.canvas.winfo_height() < self.rows.winfo_height():
            self.canvas.yview_scroll(int(-ev.delta / 120), "units")

    def _fill(self):
        for w in self.rows.winfo_children():
            w.destroy()
        self.buttons: dict[str, ttk.Button] = {}
        wall = self.app.wall_for_settings()
        combos = {actions.action_id(b["action"]): b["combo"] for b in self.app.settings.bindings}
        sections = actions.catalog(wall)
        listed = {actions.action_id(a) for _, acts in sections for a in acts}
        stale = [b["action"] for b in self.app.settings.bindings if actions.action_id(b["action"]) not in listed]
        if stale:
            sections.append(("Not on the wall right now", stale))
        if not wall.known:
            ttk.Label(self.rows, text="Connect to the hub to list the rotation's screens and news categories.",
                      style="Faint.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        r = 1
        for title, acts in sections:
            ttk.Label(self.rows, text=title, style="Section.TLabel").grid(row=r, column=0, sticky="w", pady=(10, 2))
            r += 1
            for a in acts:
                aid = actions.action_id(a)
                combo = combos.get(aid)
                ttk.Label(self.rows, text=actions.label(a, wall)).grid(row=r, column=0, sticky="w", padx=(8, 8))
                b = ttk.Button(self.rows, width=26, text=combo_label(combo) if combo else "—",
                               command=lambda aid=aid, a=a: self._record(aid, a))
                b.grid(row=r, column=1, sticky="e", pady=1)
                self.buttons[aid] = b
                ttk.Button(self.rows, text="✕", width=3, state="normal" if combo else "disabled",
                           command=lambda aid=aid: self._clear(aid)).grid(row=r, column=2, padx=(4, 0))
                r += 1
                err = combo and self.errors.get(combo)
                if err:
                    ttk.Label(self.rows, text=f"Not active: {err}", style="Error.TLabel").grid(
                        row=r, column=0, columnspan=3, sticky="w", padx=(8, 0))
                    r += 1
        self.rows.columnconfigure(0, weight=1)

    # -- events ---------------------------------------------------------------------------------
    def _poll(self):
        if not self.root:
            return
        try:
            while True:
                msg = self.inbox.get_nowait()
                if msg[0] == "lift":
                    self.root.deiconify()
                    self.root.lift()
                    self.root.focus_force()
                elif msg[0] == "recorded":
                    self._recorded(msg[1], msg[2], msg[3])
                elif msg[0] == "connected":
                    self.conn_status.configure(text=msg[1])
                    self._fill()
                elif msg[0] == "refresh":
                    self._fill()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _record(self, aid, action):
        if self.recording == aid:
            self.app.hotkeys.cancel_record()
            return
        if self.recording:
            self._fill()                            # put the box that was recording back as it was
        self.recording = aid
        self.buttons[aid].configure(text="Press a combination…", style="Recording.TButton")
        self.status.configure(text=f"Recording a hotkey for “{actions.label(action, self.app.wall_for_settings())}”. "
                                   "Esc cancels.")
        self.app.hotkeys.record(lambda combo, aid=aid, action=action: self.inbox.put(("recorded", aid, action, combo)))

    def _recorded(self, aid, action, combo):
        if self.recording != aid:
            return                                  # superseded: another box was clicked meanwhile
        self.recording = None
        if combo is None:
            self.status.configure(text="")
            self._fill()
            return
        c = Combo.parse(combo)
        if c and c.problem():
            self.status.configure(text=c.problem())
            self._fill()
            return
        wall = self.app.wall_for_settings()
        b = self.app.settings.bindings
        before = list(b)
        moved = [x for x in b if x["combo"] == combo and actions.action_id(x["action"]) != aid]
        b[:] = [x for x in b if x["combo"] != combo and actions.action_id(x["action"]) != aid]
        b.append({"combo": combo, "action": action})
        self.errors = self.app.apply_bindings()
        note = f"{combo_label(combo)} → {actions.label(action, wall)}."
        if moved:
            note += f" (Taken off “{actions.label(moved[0]['action'], wall)}”.)"
        if combo in self.errors:                    # refused: put back what was there before
            note = f"{combo_label(combo)} can't be used: {self.errors[combo]}. Try another."
            b[:] = before
            self.errors = self.app.apply_bindings()
        self.status.configure(text=note)
        self._fill()

    def _clear(self, aid):
        b = self.app.settings.bindings
        b[:] = [x for x in b if actions.action_id(x["action"]) != aid]
        self.errors = self.app.apply_bindings()
        self.status.configure(text="Hotkey removed.")
        self._fill()

    def _connect(self):
        url = self.url.get().strip()
        if not url:
            return
        if "://" not in url:
            url = "http://" + url
        if url.count(":") < 2:
            url += ":8800"
        self.url.set(url)
        self.conn_status.configure(text="Connecting…")
        threading.Thread(target=lambda: self.inbox.put(("connected", self.app.set_hub_url(url))), daemon=True).start()

    def _toggle_autostart(self):
        try:
            settings.set_autostart(self.autostart.get())
            self.status.configure(text="Starts with Windows." if self.autostart.get() else "Won't start with Windows.")
        except OSError as e:
            self.status.configure(text=f"Couldn't change that: {e}")
            self.autostart.set(settings.autostart_enabled())

    def _close(self):
        if self.recording:
            self.app.hotkeys.cancel_record()
        self.root.destroy()
