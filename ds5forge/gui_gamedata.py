"""Game Data tab: live game values + the Find-a-value wizard."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import APP_NAME, gamedata
from .integrations.kingdom_hearts import KingdomHeartsIntegration
from .memory import MemoryError_, open_process
from .scanner import Scanner

SIZES = {"4 bytes (try first)": 4, "2 bytes": 2, "1 byte": 1}
FIELDS = ("hp", "max_hp", "mp", "max_mp")
LIST_BG = "#14161c"

STEPS = (
    "1. Load your save and stand somewhere safe at FULL HP.  2. Press Start scan.  "
    "3. Take a hit, then press 'It went DOWN'.  4. Stand still a few seconds, press 'It DIDN'T change'.  "
    "5. Heal (potion/Cure), press 'It went UP'.  Repeat 3-5 until only a few addresses are left - "
    "the right one follows your HP live. Select it and press Save. If nothing is left, Reset and try '2 bytes'."
)


class GameDataTab:
    def __init__(self, app, nb):
        self.app = app
        self.engine = app.engine
        self.mem = None
        self.game: gamedata.GameData | None = None
        self.version: gamedata.Version | None = None
        self.scanner: Scanner | None = None
        self.busy = False
        self._cands: list[tuple[int, int]] = []

        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Game Data")
        tab.columnconfigure(0, weight=1)

        card = app._card(tab, "Game")
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        top = ttk.Frame(card, style="Card.TFrame")
        top.pack(fill="x")
        self.lbl_game = ttk.Label(top, text="Not attached", style="Card.TLabel", wraplength=640, justify="left")
        self.lbl_game.pack(side="left", anchor="w")
        ttk.Button(top, text="Attach to running game", command=self.attach).pack(side="right")
        self.lbl_values = ttk.Label(card, text="", style="Muted.TLabel", font=("Consolas", 10), justify="left")
        self.lbl_values.pack(anchor="w", pady=(6, 0))

        wiz = app._card(tab, "Find a value (for HP etc. that aren't known yet - Birth by Sleep)")
        wiz.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        tab.rowconfigure(1, weight=1)
        ttk.Label(wiz, text=STEPS, style="Muted.TLabel", wraplength=820, justify="left").pack(anchor="w")
        row = ttk.Frame(wiz, style="Card.TFrame")
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="Value", style="Card.TLabel").pack(side="left")
        self.var_field = tk.StringVar(value="hp")
        ttk.Combobox(row, textvariable=self.var_field, values=FIELDS, state="readonly", width=8).pack(side="left", padx=6)
        ttk.Label(row, text="Size", style="Card.TLabel").pack(side="left", padx=(8, 0))
        self.var_size = tk.StringVar(value=next(iter(SIZES)))
        ttk.Combobox(row, textvariable=self.var_size, values=list(SIZES), state="readonly", width=18).pack(side="left", padx=6)
        self.var_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Search all memory (only if the normal scan finds nothing)",
                        variable=self.var_all).pack(side="left", padx=(12, 0))
        self.lbl_count = ttk.Label(row, text="", style="Card.TLabel")
        self.lbl_count.pack(side="right")

        btns = ttk.Frame(wiz, style="Card.TFrame")
        btns.pack(fill="x")
        for text, cmd in (("Start scan", self.start), ("It went DOWN", lambda: self.step("decreased")),
                          ("It went UP", lambda: self.step("increased")),
                          ("It DIDN'T change", lambda: self.step("unchanged")), ("Reset", self.reset)):
            ttk.Button(btns, text=text, command=cmd).pack(side="left", padx=2)

        self.lst = tk.Listbox(wiz, bg=LIST_BG, fg="#e8eaf0", selectbackground="#3d7bff", borderwidth=0,
                              highlightthickness=0, font=("Consolas", 10), height=10)
        self.lst.pack(fill="both", expand=True, pady=6)
        ttk.Button(wiz, text="Save selected address", command=self.save).pack(anchor="e")

    # --------------------------------------------------------------- attach
    def _engine_kh(self) -> KingdomHeartsIntegration | None:
        integ = self.engine.integration
        return integ if isinstance(integ, KingdomHeartsIntegration) and integ.mem is not None else None

    def attach(self) -> None:
        kh = self._engine_kh()
        if kh is not None:
            self.mem, self.game, self.version = kh.mem, kh.game, kh.version
            return
        for game in gamedata.all_games():
            try:
                mem = open_process(game.process)
            except Exception as exc:  # pragma: no cover - Windows only
                messagebox.showerror(APP_NAME, str(exc))
                return
            if mem is not None:
                self.mem, self.game, self.version = mem, game, game.detect(mem)
                return
        messagebox.showinfo(APP_NAME, "No supported game is running. Start the game first.")

    # --------------------------------------------------------------- wizard
    def _run(self, work, done) -> None:
        """Run a scan step off the UI thread; the UI thread polls for the result."""
        if self.busy:
            return
        self.busy = True
        self.lbl_count.configure(text="Working...")
        box: dict = {}

        def bg():
            try:
                box["result"] = work()
            except (MemoryError_, OSError, ValueError) as exc:
                box["error"] = exc
            box["done"] = True

        def poll():
            if not box.get("done"):
                self.app.root.after(50, poll)
                return
            self._finish(done, box.get("result"), box.get("error"))

        threading.Thread(target=bg, daemon=True).start()
        self.app.root.after(50, poll)

    def _finish(self, done, result, err) -> None:
        self.busy = False
        if err is not None:
            messagebox.showerror(APP_NAME, "Scan failed: %s" % err)
            self.lbl_count.configure(text="")
            return
        done(result)

    def start(self) -> None:
        if self.mem is None:
            self.attach()
            if self.mem is None:
                return
        size = SIZES[self.var_size.get()]
        self.scanner = Scanner(self.mem, size=size, module_only=not self.var_all.get())
        self._run(self.scanner.start, self._show)

    def step(self, change: str) -> None:
        if self.scanner is None:
            messagebox.showinfo(APP_NAME, "Press Start scan first.")
            return
        self._run(lambda: self.scanner.step(change), self._show)

    def reset(self) -> None:
        self.scanner = None
        self._cands = []
        self.lst.delete(0, "end")
        self.lbl_count.configure(text="")

    def _show(self, count) -> None:
        self.lbl_count.configure(text="%s candidate addresses" % format(count, ","))
        self._cands = self.scanner.candidates(30) if count <= 30 else []
        self._fill_list()

    def _fill_list(self) -> None:
        sel = self.lst.curselection()
        self.lst.delete(0, "end")
        if not self._cands:
            if self.scanner is not None and self.scanner.count() > 30:
                self.lst.insert("end", "Keep going - too many candidates to list yet.")
            return
        for off, _ in self._cands:
            now = self.scanner.read_offset(off)
            self.lst.insert("end", "0x%08X   value now: %s" % (off, now))
        if sel:
            self.lst.selection_set(sel[0])

    def save(self) -> None:
        sel = self.lst.curselection()
        if not sel or not self._cands or self.game is None:
            messagebox.showinfo(APP_NAME, "Select an address from the list first.")
            return
        off = self._cands[sel[0]][0]
        name = self.var_field.get()
        kind = gamedata.kind_for_size(self.scanner.size)
        vname = gamedata.save_user_field(self.game, self.mem, self.version, name, off, kind)
        self.game = gamedata.load(self.game.id)
        self.version = self.game.detect(self.mem)
        integ = self.engine.integration
        if isinstance(integ, KingdomHeartsIntegration):
            integ.reload()
        note = ""
        if not self.scanner.module_only:
            note = ("\n\nThis address is outside the game program, so it may move when you restart the game. "
                    "If HP stops working after a restart, run the wizard again.")
        messagebox.showinfo(APP_NAME, "Saved %s = 0x%X for %s (%s).\nIt's used from now on.%s" % (
            name, off, self.game.name, vname, note))

    # -------------------------------------------------------------- refresh
    def refresh(self) -> None:
        kh = self._engine_kh()
        if kh is not None and self.mem is None:
            self.mem, self.game, self.version = kh.mem, kh.game, kh.version
        if self.mem is not None and not self.mem.alive():
            self.mem = self.game = self.version = None
            self.reset()
        if self.mem is None:
            self.lbl_game.configure(text="Not attached. Start a supported game (Birth by Sleep, KH1, KH2).")
            self.lbl_values.configure(text="")
            return
        ver = self.version.name if self.version else "unknown version (only the wizard can be used)"
        self.lbl_game.configure(text="%s  -  %s  -  %s" % (self.game.name, ver, self.mem.name))
        if self.version is not None:
            parts = []
            for name, fld in self.version.fields.items():
                try:
                    val = fld.read(self.mem)
                except (MemoryError_, OSError):
                    val = "?"
                parts.append("%s = %s" % (name, round(val, 2) if isinstance(val, float) else val))
            self.lbl_values.configure(text="   ".join(parts) or "(no known values for this version yet)")
        if self._cands and not self.busy:
            self._fill_list()

