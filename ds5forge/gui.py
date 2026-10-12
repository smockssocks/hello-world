"""Desktop window (tkinter - ships with Python, no extra install)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

from . import APP_NAME, __version__
from .engine import Engine
from .gui_gamedata import GameDataTab
from .haptics import synth
from .profiles import Profile, save_user_profile, user_profile_dir
from .settings import HAPTICS_MODES
from .triggers import _BUILDERS, EFFECT_TYPES, PARAM_RANGES, PRESETS, TriggerEffect

BG = "#14161c"
PANEL = "#1d2029"
FG = "#e8eaf0"
MUTED = "#9aa0b0"
ACCENT = "#3d7bff"
GOOD = "#3ccf7a"
WARN = "#ffb648"


_SYSTEM_PROCESSES = {
    "svchost.exe", "explorer.exe", "conhost.exe", "csrss.exe", "dwm.exe", "lsass.exe", "services.exe",
    "smss.exe", "wininit.exe", "winlogon.exe", "runtimebroker.exe", "searchhost.exe", "sihost.exe",
    "taskhostw.exe", "ctfmon.exe", "fontdrvhost.exe", "dllhost.exe", "audiodg.exe", "spoolsv.exe",
    "system", "registry", "startmenuexperiencehost.exe", "textinputhost.exe", "shellexperiencehost.exe",
    "securityhealthservice.exe", "msmpeng.exe", "wudfhost.exe", "dashost.exe", "lsaiso.exe",
    "python.exe", "pythonw.exe", "ds5forge.exe",
}


class LogHandler(logging.Handler):
    def __init__(self, sink):
        super().__init__(logging.INFO)
        self.sink = sink
        self.count = 0

    def emit(self, record):
        self.sink.append(self.format(record))
        self.count += 1
        if len(self.sink) > 1000:
            del self.sink[:500]


class App:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.root = tk.Tk()
        self.root.title("%s %s" % (APP_NAME, __version__))
        self.root.geometry("900x640")
        self.root.minsize(780, 560)
        self.root.configure(bg=BG)
        self._log_lines: list[str] = []
        self._save_job = None
        self._log_handler = handler = LogHandler(self._log_lines)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        self._style()
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_dashboard(nb)
        self._build_profiles(nb)
        self._build_trigger_lab(nb)
        self._build_haptics_lab(nb)
        self.gamedata_tab = GameDataTab(self, nb)
        self._build_games(nb)
        self._build_log(nb)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(200, self._refresh)

    # ----------------------------------------------------------------- style
    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL,
                    font=("Segoe UI", 10))
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(14, 6))
        s.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=PANEL)
        s.configure("Card.TLabel", background=PANEL)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        s.configure("H.TLabel", background=PANEL, font=("Segoe UI Semibold", 12))
        s.configure("Big.TLabel", background=PANEL, font=("Segoe UI Semibold", 16))
        s.configure("TButton", background=ACCENT, foreground="white", borderwidth=0, padding=(10, 5))
        s.map("TButton", background=[("active", "#5a90ff")])
        s.configure("TCheckbutton", background=PANEL)
        s.map("TCheckbutton", background=[("active", PANEL)])
        s.configure("TCombobox", fieldbackground=BG, background=PANEL, foreground=FG, arrowcolor=FG)
        s.map("TCombobox", fieldbackground=[("readonly", BG)], foreground=[("readonly", FG)],
              selectbackground=[("readonly", BG)], selectforeground=[("readonly", FG)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        s.configure("Horizontal.TScale", background=ACCENT, troughcolor=BG)
        s.configure("TCheckbutton", indicatorbackground=BG, indicatorforeground=FG)
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BG)

    def _card(self, parent, title: str):
        f = ttk.Frame(parent, style="Card.TFrame", padding=12)
        ttk.Label(f, text=title, style="H.TLabel").pack(anchor="w", pady=(0, 6))
        return f

    # ------------------------------------------------------------- dashboard
    def _build_dashboard(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Dashboard")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        status = self._card(tab, "Controller")
        status.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.lbl_conn = ttk.Label(status, text="Searching...", style="Big.TLabel")
        self.lbl_conn.pack(anchor="w")
        self.lbl_battery = ttk.Label(status, text="", style="Muted.TLabel")
        self.lbl_battery.pack(anchor="w")
        self.lbl_haptics = ttk.Label(status, text="", style="Card.TLabel", wraplength=380)
        self.lbl_haptics.pack(anchor="w", pady=(8, 0))
        self.lbl_audio = ttk.Label(status, text="", style="Muted.TLabel", wraplength=380)
        self.lbl_audio.pack(anchor="w")
        self.lbl_xbox = ttk.Label(status, text="", style="Muted.TLabel", wraplength=380)
        self.lbl_xbox.pack(anchor="w")
        self.lbl_mods = ttk.Label(status, text="", style="Muted.TLabel", wraplength=380)
        self.lbl_mods.pack(anchor="w")

        meters = ttk.Frame(status, style="Card.TFrame")
        meters.pack(fill="x", pady=(10, 0))
        self.bar_l2 = self._meter(meters, "L2", 0)
        self.bar_r2 = self._meter(meters, "R2", 1)
        self.bar_hap = self._meter(meters, "Haptics", 2)

        game = self._card(tab, "Game")
        game.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        self.lbl_profile = ttk.Label(game, text="", style="Big.TLabel", wraplength=380)
        self.lbl_profile.pack(anchor="w")
        self.lbl_game = ttk.Label(game, text="", style="Muted.TLabel", wraplength=380)
        self.lbl_game.pack(anchor="w")
        self.lbl_integration = ttk.Label(game, text="", style="Card.TLabel", wraplength=380)
        self.lbl_integration.pack(anchor="w", pady=(8, 0))

        st = self.engine.settings
        opts = self._card(tab, "Settings")
        opts.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=4, pady=4)
        grid = ttk.Frame(opts, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        ttk.Label(grid, text="Haptics", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.var_mode = tk.StringVar(value=st.haptics_mode)
        mode = ttk.Combobox(grid, textvariable=self.var_mode, values=HAPTICS_MODES, state="readonly", width=12)
        mode.grid(row=0, column=1, sticky="w", padx=4)
        mode.bind("<<ComboboxSelected>>", lambda e: self._set("haptics_mode", self.var_mode.get(), reconfigure=True))

        self.var_bt = tk.BooleanVar(value=st.bt_haptics)
        ttk.Checkbutton(grid, text="HD haptics over Bluetooth (experimental)", variable=self.var_bt,
                        command=lambda: self._set("bt_haptics", self.var_bt.get(), reconfigure=True)
                        ).grid(row=0, column=2, columnspan=2, sticky="w", padx=4)

        self.var_hint = self._slider(grid, "Haptic strength", 1, 0, st.haptic_intensity, 0, 2, "haptic_intensity")
        self.var_tint = self._slider(grid, "Trigger strength", 1, 2, st.trigger_intensity, 0, 1, "trigger_intensity")

        self.var_auto = tk.BooleanVar(value=st.auto_profiles)
        ttk.Checkbutton(grid, text="Switch profile automatically when a game starts", variable=self.var_auto,
                        command=lambda: self._set("auto_profiles", self.var_auto.get())
                        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        self.var_audio = tk.BooleanVar(value=st.audio_capture)
        ttk.Checkbutton(grid, text="Use game audio for haptics", variable=self.var_audio,
                        command=self._toggle_audio).grid(row=2, column=2, columnspan=2, sticky="w", padx=4)

        self.var_dsx = tk.BooleanVar(value=st.dsx_server)
        ttk.Checkbutton(grid, text="Accept DSX game mods (Skyrim, Cyberpunk...)", variable=self.var_dsx,
                        command=self._toggle_dsx).grid(row=4, column=0, columnspan=2, sticky="w", padx=4)
        ttk.Button(grid, text="Lightbar colour...", command=self._pick_color).grid(row=3, column=0, sticky="w", padx=4, pady=6)
        ttk.Label(grid, text="LED brightness", style="Card.TLabel").grid(row=3, column=2, sticky="w", padx=4)
        self.var_bright = tk.StringVar(value=["Bright", "Medium", "Dim"][st.led_brightness])
        bright = ttk.Combobox(grid, textvariable=self.var_bright, values=["Bright", "Medium", "Dim"], state="readonly", width=10)
        bright.grid(row=3, column=3, sticky="w", padx=4)
        bright.bind("<<ComboboxSelected>>", lambda e: self._set(
            "led_brightness", ["Bright", "Medium", "Dim"].index(self.var_bright.get())))

    def _meter(self, parent, label, row):
        ttk.Label(parent, text=label, style="Muted.TLabel", width=8).grid(row=row, column=0, sticky="w")
        bar = ttk.Progressbar(parent, maximum=1.0, length=260)
        bar.grid(row=row, column=1, sticky="ew", pady=2)
        return bar

    def _slider(self, parent, label, row, col, value, lo, hi, key):
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=col, sticky="w", padx=4, pady=4)
        var = tk.DoubleVar(value=value)
        ttk.Scale(parent, from_=lo, to=hi, variable=var,
                  command=lambda v: self._set(key, round(float(v), 2), save=False)
                  ).grid(row=row, column=col + 1, sticky="ew", padx=4)
        return var

    def _set(self, key, value, reconfigure=False, save=True):
        setattr(self.engine.settings, key, value)
        if save:
            self.engine.settings.save()
        else:
            if self._save_job:
                self.root.after_cancel(self._save_job)
            self._save_job = self.root.after(800, self.engine.settings.save)
        if reconfigure:
            self.engine.reconfigure_output()

    def _toggle_audio(self):
        on = self.var_audio.get()
        self._set("audio_capture", on)
        if on:
            self.engine.start_audio_capture()
        else:
            self.engine.capture.stop()

    def _toggle_dsx(self):
        on = self.var_dsx.get()
        self._set("dsx_server", on)
        if on:
            self.engine.start_dsx_server()
        else:
            self.engine.dsx.stop()

    def _pick_color(self):
        rgb, _ = colorchooser.askcolor(color="#%02x%02x%02x" % tuple(self.engine.settings.lightbar_color),
                                       title="Lightbar colour")
        if rgb:
            self._set("lightbar_color", [int(c) for c in rgb])

    # -------------------------------------------------------------- profiles
    def _build_profiles(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Profiles")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)
        left = self._card(tab, "Profiles")
        left.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.lst_profiles = tk.Listbox(left, bg=BG, fg=FG, selectbackground=ACCENT, borderwidth=0,
                                       highlightthickness=0, width=32, activestyle="none")
        self.lst_profiles.pack(fill="both", expand=True)
        self.lst_profiles.bind("<<ListboxSelect>>", lambda e: self._show_profile())
        btns = ttk.Frame(left, style="Card.TFrame")
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Use when no game runs", command=self._use_profile).pack(fill="x", pady=2)
        ttk.Button(btns, text="Add running game...", command=self._add_running_game).pack(fill="x", pady=2)
        ttk.Button(btns, text="Reload", command=self._reload_profiles).pack(fill="x", pady=2)
        ttk.Button(btns, text="Open my profiles folder", command=self._open_profiles_dir).pack(fill="x", pady=2)

        right = self._card(tab, "Profile details (JSON - edit and save as your own)")
        right.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        self.lbl_notes = ttk.Label(right, text="", style="Muted.TLabel", wraplength=480, justify="left")
        self.lbl_notes.pack(anchor="w", pady=(0, 6))
        self.txt_profile = tk.Text(right, bg=BG, fg=FG, insertbackground=FG, borderwidth=0,
                                   font=("Consolas", 10), wrap="none", height=20)
        self.txt_profile.pack(fill="both", expand=True)
        ttk.Button(right, text="Save as my profile", command=self._save_profile).pack(anchor="e", pady=(8, 0))
        self._fill_profiles()

    def _fill_profiles(self):
        self.lst_profiles.delete(0, "end")
        self._profile_names = sorted(self.engine.profiles)
        for name in self._profile_names:
            prof = self.engine.profiles[name]
            self.lst_profiles.insert("end", name + ("" if prof.builtin else "  (yours)"))

    def _selected_profile(self) -> Profile | None:
        sel = self.lst_profiles.curselection()
        return self.engine.profiles.get(self._profile_names[sel[0]]) if sel else None

    def _show_profile(self):
        prof = self._selected_profile()
        if prof is None:
            return
        self.lbl_notes.configure(text=prof.notes or "")
        self.txt_profile.delete("1.0", "end")
        self.txt_profile.insert("1.0", json.dumps(prof.to_dict(), indent=2))

    def _use_profile(self):
        prof = self._selected_profile()
        if prof:
            self.engine.select_profile(prof.name)

    def _save_profile(self):
        try:
            prof = Profile.from_dict(json.loads(self.txt_profile.get("1.0", "end")))
        except Exception as exc:
            messagebox.showerror(APP_NAME, "That profile isn't valid:\n%s" % exc)
            return
        path = save_user_profile(prof)
        self.engine.reload_profiles()
        self._fill_profiles()
        messagebox.showinfo(APP_NAME, "Saved to %s" % path)

    def _add_running_game(self):
        """Pick a running program and add it to the profile being edited."""
        from .profiles import running_processes
        try:
            data = json.loads(self.txt_profile.get("1.0", "end"))
        except Exception:
            messagebox.showinfo(APP_NAME, "Select a profile first.")
            return
        names = sorted(n for n in running_processes() if n.endswith(".exe") and n not in _SYSTEM_PROCESSES)
        win = tk.Toplevel(self.root)
        win.title("Add running game")
        win.configure(bg=BG)
        win.geometry("380x420")
        ttk.Label(win, text="Start your game, then pick its .exe:").pack(anchor="w", padx=10, pady=(10, 4))
        lst = tk.Listbox(win, bg=PANEL, fg=FG, selectbackground=ACCENT, borderwidth=0, highlightthickness=0)
        lst.pack(fill="both", expand=True, padx=10)
        for n in names:
            lst.insert("end", n)

        def add():
            sel = lst.curselection()
            if not sel:
                return
            exe = names[sel[0]]
            data.setdefault("exe", [])
            if exe.lower() not in (e.lower() for e in data["exe"]):
                data["exe"].append(exe)
            self.txt_profile.delete("1.0", "end")
            self.txt_profile.insert("1.0", json.dumps(data, indent=2))
            win.destroy()
            messagebox.showinfo(APP_NAME, "Added %s. Click 'Save as my profile' to keep it." % exe)

        ttk.Button(win, text="Add to profile", command=add).pack(pady=10)

    def _reload_profiles(self):
        self.engine.reload_profiles()
        self._fill_profiles()

    def _open_profiles_dir(self):
        path = str(user_profile_dir())
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", path])

    # ----------------------------------------------------------- trigger lab
    def _build_trigger_lab(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Trigger Lab")
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        top = ttk.Frame(tab, style="Card.TFrame", padding=10)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=4)
        self.var_lab = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Live: override the triggers with these effects", variable=self.var_lab,
                        command=self._apply_lab).pack(side="left")
        ttk.Label(top, text="  Try one, then 'Copy JSON' to paste it into a profile.", style="Muted.TLabel").pack(side="left")
        self.lab = {}
        for col, side in enumerate(("left", "right")):
            card = self._card(tab, "L2 (left trigger)" if side == "left" else "R2 (right trigger)")
            card.grid(row=1, column=col, sticky="nsew", padx=4, pady=4)
            preset = tk.StringVar(value="Off")
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x")
            ttk.Label(row, text="Preset", style="Card.TLabel").pack(side="left")
            cb = ttk.Combobox(row, textvariable=preset, values=list(PRESETS), state="readonly", width=18)
            cb.pack(side="left", padx=6)
            kind = tk.StringVar(value="off")
            ttk.Label(row, text="Type", style="Card.TLabel").pack(side="left", padx=(8, 0))
            kb = ttk.Combobox(row, textvariable=kind, values=[t for t in EFFECT_TYPES if not t.startswith("multi")],
                              state="readonly", width=11)
            kb.pack(side="left", padx=6)
            params = ttk.Frame(card, style="Card.TFrame")
            params.pack(fill="x", pady=8)
            ttk.Button(card, text="Copy JSON", command=lambda s=side: self._copy_lab(s)).pack(anchor="e")
            self.lab[side] = {"kind": kind, "preset": preset, "frame": params, "vars": {}}
            cb.bind("<<ComboboxSelected>>", lambda e, s=side: self._lab_preset(s))
            kb.bind("<<ComboboxSelected>>", lambda e, s=side: self._lab_kind(s))
            self._lab_kind(side)

    def _lab_kind(self, side, values=None):
        lab = self.lab[side]
        for w in lab["frame"].winfo_children():
            w.destroy()
        lab["vars"] = {}
        _, names = _BUILDERS[lab["kind"].get()]
        for i, name in enumerate(names):
            lo, hi = PARAM_RANGES.get(name, (0, 10))
            default = (values or {}).get(name, min(max((lo + hi) // 2, lo), hi))
            var = tk.IntVar(value=default)
            ttk.Label(lab["frame"], text=name.replace("_", " "), style="Card.TLabel", width=14).grid(row=i, column=0, sticky="w")
            ttk.Scale(lab["frame"], from_=lo, to=hi, variable=var,
                      command=lambda v, var=var: (var.set(round(float(v))), self._apply_lab())
                      ).grid(row=i, column=1, sticky="ew", padx=4)
            ttk.Label(lab["frame"], textvariable=var, style="Muted.TLabel", width=4).grid(row=i, column=2)
            lab["vars"][name] = var
        lab["frame"].columnconfigure(1, weight=1)
        self._apply_lab()

    def _lab_preset(self, side):
        eff = PRESETS[self.lab[side]["preset"].get()]
        if eff.type.startswith("multi"):
            self.lab[side]["kind"].set("feedback")
            self._lab_kind(side)
            return
        self.lab[side]["kind"].set(eff.type)
        self._lab_kind(side, eff.params)

    def _lab_effect(self, side) -> TriggerEffect:
        lab = self.lab[side]
        return TriggerEffect(lab["kind"].get(), {k: v.get() for k, v in lab["vars"].items()})

    def _apply_lab(self):
        if not hasattr(self, "lab") or len(self.lab) < 2:
            return
        if self.var_lab.get():
            self.engine.lab_override = (self._lab_effect("left"), self._lab_effect("right"))
        else:
            self.engine.lab_override = None

    def _copy_lab(self, side):
        self.root.clipboard_clear()
        self.root.clipboard_append(json.dumps(self._lab_effect(side).to_dict()))

    # ----------------------------------------------------------- haptics lab
    def _build_haptics_lab(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Haptics Lab")
        card = self._card(tab, "Test effects")
        card.pack(fill="x", padx=4, pady=4)
        ttk.Label(card, text="Use these to check that haptics work on your connection (USB or Bluetooth).",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x")
        for i, (name, fn) in enumerate(synth.TEST_EFFECTS.items()):
            ttk.Button(grid, text=name, command=lambda f=fn: f(self.engine.mixer)).grid(
                row=i // 4, column=i % 4, sticky="ew", padx=3, pady=3)
        for c in range(4):
            grid.columnconfigure(c, weight=1)

        tone = self._card(tab, "Tone generator")
        tone.pack(fill="x", padx=4, pady=4)
        self.var_tone_on = tk.BooleanVar(value=False)
        self.var_tone_f = tk.DoubleVar(value=80)
        self.var_tone_a = tk.DoubleVar(value=0.5)
        self.var_tone_p = tk.DoubleVar(value=0.0)
        ttk.Checkbutton(tone, text="Play continuous tone", variable=self.var_tone_on, command=self._update_tone).pack(anchor="w")
        g = ttk.Frame(tone, style="Card.TFrame")
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        for i, (label, var, lo, hi) in enumerate((("Frequency (Hz)", self.var_tone_f, 20, 400),
                                                    ("Strength", self.var_tone_a, 0, 1),
                                                    ("Left / right", self.var_tone_p, -1, 1))):
            ttk.Label(g, text=label, style="Card.TLabel", width=16).grid(row=i, column=0, sticky="w")
            ttk.Scale(g, from_=lo, to=hi, variable=var, command=lambda v: self._update_tone()).grid(
                row=i, column=1, sticky="ew", padx=4, pady=3)

    def _update_tone(self):
        if self.var_tone_on.get():
            ch = self.engine.mixer.channel("lab.tone")
            ch.set(freq=self.var_tone_f.get(), amp=self.var_tone_a.get(), pan=self.var_tone_p.get())
        else:
            self.engine.mixer.set_channel("lab.tone", None)

    # ----------------------------------------------------------------- games
    def _build_games(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Game setup")
        canvas = tk.Canvas(tab, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        wheel = lambda e: canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        intro = self._card(inner, "How DS5 Forge works with your games")
        intro.pack(fill="x", padx=4, pady=4)
        ttk.Label(intro, style="Card.TLabel", wraplength=760, justify="left", text=(
            "When a game starts, DS5 Forge switches to its profile automatically. Every game gets HD "
            "haptics from its sound, and its rumble turns into HD haptics too. Free DualSense mods made "
            "for the paid DSX app (Skyrim, Cyberpunk, Fallout 4, RDR2...) work with DS5 Forge directly: "
            "keep 'Accept DSX game mods' on and install the mod as its page says, skipping the DSX part. "
            "For almost every game: turn Steam Input OFF for it (Steam > game > Properties > Controller)."
        )).pack(anchor="w")
        for prof in sorted(self.engine.profiles.values(), key=lambda pr: pr.name):
            if not prof.notes or prof.name == "Default (any game)":
                continue
            card = self._card(inner, prof.name)
            card.pack(fill="x", padx=4, pady=4)
            if prof.exe:
                ttk.Label(card, text="Detected by: " + ", ".join(prof.exe), style="Muted.TLabel",
                          wraplength=760, justify="left").pack(anchor="w")
            ttk.Label(card, text=prof.notes, style="Card.TLabel", wraplength=760,
                      justify="left").pack(anchor="w", pady=2)

    # ------------------------------------------------------------------- log
    def _build_log(self, nb):
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text="Log")
        self.txt_log = tk.Text(tab, bg=PANEL, fg=FG, borderwidth=0, font=("Consolas", 9), state="disabled")
        self.txt_log.pack(fill="both", expand=True)
        self._log_shown = 0

    # --------------------------------------------------------------- refresh
    def _refresh(self):
        s = self.engine.status()
        if s["connected"]:
            self.lbl_conn.configure(text="DualSense connected - %s" % s["transport"], foreground=GOOD)
            batt = s["battery"]
            self.lbl_battery.configure(text="Battery %s%%%s" % (batt, " (charging)" if s["charging"] else ""))
        else:
            self.lbl_conn.configure(text="Looking for a DualSense...", foreground=WARN)
            self.lbl_battery.configure(text="Connect it by USB cable or pair it over Bluetooth.")
        self.lbl_haptics.configure(text="Haptics: %s%s" % (
            s["haptics"], ("\n" + s["haptics_note"]) if s["haptics_note"] and s["haptics_note"] != s["haptics"] else ""))
        self.lbl_audio.configure(text="Game audio: %s" % s["audio"])
        self.lbl_xbox.configure(text="Xbox bridge: %s" % s["xbox"])
        self.lbl_mods.configure(text="Game mods: %s" % s["mods"])
        self.lbl_profile.configure(text=s["profile"])
        self.lbl_game.configure(text=("Detected: %s" % s["game"]) if s["game"] else "No known game running")
        self.lbl_integration.configure(text=("%s\n%s" % (s["integration"], s["integration_status"]))
                                       if s["integration"] else self._profile_summary())
        inp = self.engine.input
        self.bar_l2["value"] = inp.l2 / 255
        self.bar_r2["value"] = inp.r2 / 255
        self.bar_hap["value"] = min(s["haptic_peak"], 1.0)

        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 3 == 0:
            self.gamedata_tab.refresh()
        if self._log_handler.count != self._log_shown:
            self._log_shown = self._log_handler.count
            self.txt_log.configure(state="normal")
            self.txt_log.delete("1.0", "end")
            self.txt_log.insert("end", "\n".join(self._log_lines[-400:]))
            self.txt_log.configure(state="disabled")
            self.txt_log.see("end")
        self.root.after(100, self._refresh)

    def _profile_summary(self) -> str:
        prof = self.engine.profile
        parts = []
        if prof.hands_off:
            parts.append("The game drives the DualSense itself; DS5 Forge stays out of the way")
        if prof.audio_haptics:
            parts.append("HD haptics from game sound" + (" with hit taps" if prof.audio_hits else ""))
        if prof.xbox_bridge:
            parts.append("game rumble turned into HD haptics (virtual Xbox controller)")
        if prof.haptics == "bluetooth_only":
            parts.append("DS5 Forge haptics only when wireless")
        if self.engine.mod_text_file:
            parts.append("trigger mod file: %s" % self.engine.mod_text_file.name)
        if prof.left_trigger or prof.right_trigger or prof.rules:
            parts.append("custom trigger effects")
        return "\n".join("- " + p for p in parts) if parts else ""

    def close(self):
        self.engine.lab_override = None
        self.engine.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def run_gui(engine: Engine) -> None:
    App(engine).run()
