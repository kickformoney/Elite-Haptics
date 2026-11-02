"""Desktop control panel for Elite Haptics.

Uses only Python's standard Tk UI so the packaged application does not gain a
large GUI dependency. Telemetry and playback live in ``elite_haptics_engine.py``.
"""

from __future__ import annotations

import importlib.util
import ctypes
import json
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


def enable_crisp_windows_scaling():
    """Opt into per-monitor DPI rendering before Tk creates a window."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


enable_crisp_windows_scaling()


HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_PATH = os.path.join(HERE, "elite_haptics_engine.py")
spec = importlib.util.spec_from_file_location("elite_haptics_engine", ENGINE_PATH)
engine = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(engine)

EFFECTS_GUIDE_PATH = os.path.join(HERE, "EFFECTS_GUIDE.md")
TRIGGERS_GUIDE_PATH = os.path.join(HERE, "TRIGGERS_GUIDE.md")

THEME_OPTIONS = {
    "dark_blue": "Dark Blue",
    "light": "Light Blue",
    "elite_amber": "Elite",
}
THEME_IDS_BY_LABEL = {label: theme_id for theme_id, label in THEME_OPTIONS.items()}


def normalize_theme_id(value):
    """Translate legacy dark/light settings and display labels to theme IDs."""
    if value == "dark":
        return "dark_blue"
    if value in THEME_OPTIONS:
        return value
    return THEME_IDS_BY_LABEL.get(value, "dark_blue")

# In a one-file PyInstaller build the bundled engine is extracted to a
# temporary directory. Keep user-editable files beside the executable instead
# so they remain visible and survive application restarts.
if getattr(sys, "frozen", False):
    application_dir = os.path.dirname(sys.executable)
    engine.CONFIG_PATH = os.path.join(application_dir, "haptics_config.json")
    external_effects_path = os.path.join(application_dir, "effects.json")
    external_triggers_path = os.path.join(application_dir, "triggers.json")
    external_guide_path = os.path.join(application_dir, "EFFECTS_GUIDE.md")
    external_triggers_guide_path = os.path.join(application_dir, "TRIGGERS_GUIDE.md")
    try:
        if not os.path.exists(external_effects_path):
            shutil.copyfile(engine.EFFECTS_CONFIG_PATH, external_effects_path)
        if not os.path.exists(external_triggers_path):
            shutil.copyfile(engine.TRIGGERS_CONFIG_PATH, external_triggers_path)
        if not os.path.exists(external_guide_path) and os.path.exists(EFFECTS_GUIDE_PATH):
            shutil.copyfile(EFFECTS_GUIDE_PATH, external_guide_path)
        if (not os.path.exists(external_triggers_guide_path)
                and os.path.exists(TRIGGERS_GUIDE_PATH)):
            shutil.copyfile(TRIGGERS_GUIDE_PATH, external_triggers_guide_path)
        engine.EFFECTS_CONFIG_PATH = external_effects_path
        engine.TRIGGERS_CONFIG_PATH = external_triggers_path
        engine.TRIGGERS_CONFIG_MTIME = None
        engine.load_trigger_definitions()
        EFFECTS_GUIDE_PATH = external_guide_path
        TRIGGERS_GUIDE_PATH = external_triggers_guide_path
    except OSError:
        # A read-only installation can still use the bundled catalog.
        pass


def pretty_effect(effect_id):
    configured_name = engine.get_effect_display_name(effect_id)
    if configured_name:
        return configured_name
    value = effect_id
    for prefix in ("other_", "status_", "station_", "trade_", "travel_", "hit_"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    label = value.replace("_", " ").title()
    replacements = {
        "Shield": "Shield Hit", "Hull": "Hull Hit",
        "Afmurepair": "AFMU Repair", "Fsdengage": "FSD Engage",
        "Fsdjump": "FSD Jump", "Srv": "SRV", "Cockpitbreach": "Cockpit Breach",
        "Systemsshutdown": "Systems Shutdown", "Restockvehicle": "Restock Vehicle",
        "Ejectcargo": "Eject Cargo", "Approachbody": "Approach Body",
        "Leavebody": "Leave Body", "Shieldsup": "Shields Up",
        "Shieldsdown": "Shields Down", "Hardpointsdeployed": "Hardpoints Deployed",
        "Hardpointsretracted": "Hardpoints Retracted",
        "Landinggeardeployed": "Landing Gear Deployed",
        "Landinggearretracted": "Landing Gear Retracted",
    }
    return replacements.get(label, label)


def trigger_details(trigger_id):
    return engine.TRIGGER_METADATA.get(
        trigger_id,
        (trigger_id.split("_", 1)[0].title(), trigger_id.replace("_", " ").title(), False, ""))


class Runtime:
    def __init__(self):
        self.observer = None
        self.stop_event = threading.Event()
        self.status_thread = None

    def start(self):
        engine.init_audio()
        handler = engine.JournalHandler()
        self.observer = engine.Observer()
        self.observer.schedule(handler, path=engine.JOURNAL_DIR, recursive=False)
        self.observer.start()
        watcher = engine.StatusWatcher()

        def poll_status():
            while not self.stop_event.wait(0.25):
                watcher.check()

        self.status_thread = threading.Thread(target=poll_status, daemon=True)
        self.status_thread.start()

    def stop(self):
        self.stop_event.set()
        for name in list(engine.continuous_effects):
            engine.stop_continuous_effect(name)
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2)
        if engine.STREAM:
            engine.STREAM.stop()
            engine.STREAM.close()
            engine.STREAM = None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        engine.load_config()
        self.runtime = Runtime()
        self.title("Elite Haptics")
        try:
            self.geometry(engine.WINDOW_GEOMETRY)
        except tk.TclError:
            engine.WINDOW_GEOMETRY = "940x780"
            self.geometry(engine.WINDOW_GEOMETRY)
        self.minsize(820, 700)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.style = ttk.Style(self)
        self._geometry_save_job = None
        self.monitor_queue = queue.Queue()
        self.monitor_counts = {}
        self.monitor_count_items = {}
        self.pad_effect_combos = []

        self.status_text = tk.StringVar(value="Stopped")
        self.trigger_var = tk.StringVar()
        self.effect_var = tk.StringVar()
        self.intensity_var = tk.DoubleVar(value=1.0)
        self.warning_var = tk.StringVar()
        self.master_var = tk.DoubleVar(value=engine.MASTER_VOLUME)
        self.pad_vars = [tk.StringVar() for _ in range(10)]
        self.pad_strength_vars = [tk.DoubleVar(value=1.0) for _ in range(10)]
        self.mode_var = tk.StringVar(value="Default")
        self.filter_var = tk.StringVar()
        self.category_filter_var = tk.StringVar(value="All categories")
        self.device_var = tk.StringVar(value=engine.OUTPUT_DEVICE_NAME)
        self.journal_var = tk.StringVar(value=engine.JOURNAL_DIR)
        initial_theme = normalize_theme_id(engine.COLOR_THEME)
        self.theme_var = tk.StringVar(value=THEME_OPTIONS[initial_theme])
        self.sort_column = "category"
        self.sort_reverse = False
        self.loading_pads = False

        self.effects = sorted(engine.get_effect_registry())
        self.effect_display_to_id = {pretty_effect(effect): effect for effect in self.effects}
        self.effect_choices = sorted(self.effect_display_to_id)
        if not engine.TEST_PAD_MODES:
            engine.TEST_PAD_MODES = {"Default": {}}
        if self.mode_var.get() not in engine.TEST_PAD_MODES:
            self.mode_var.set(sorted(engine.TEST_PAD_MODES)[0])
        self.active_mode_name = self.mode_var.get()
        self.trigger_display_to_id = {
            details[1] + ("*" if details[2] else ""): trigger_id
            for trigger_id, details in engine.TRIGGER_METADATA.items()
        }
        self.trigger_id_to_display = {value: key for key, value in self.trigger_display_to_id.items()}
        self.triggers = sorted(self.trigger_display_to_id)
        self._build()
        self.apply_theme(save=False)
        self._load_pads()
        self.refresh_bindings()
        engine.add_monitor_listener(self.monitor_queue.put)
        self.bind("<Configure>", self.on_window_configure, add="+")
        self.after(150, self.start_runtime)
        self.after(200, self.poll_monitor_events)

    def _build(self):
        header = ttk.Frame(self, style="Titlebar.TFrame", padding=(12, 0))
        header.pack(fill="x", padx=18, pady=(14, 0))
        ttk.Label(
            header, text="◈  ELITE  /  HAPTICS", style="Title.TLabel"
        ).pack(side="left", pady=10)
        self.status_label = ttk.Label(
            header, textvariable=self.status_text, style="HeaderStatus.TLabel"
        )
        self.status_label.pack(side="right", pady=10)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=18, pady=(12, 14))
        bindings_tab = ttk.Frame(self.notebook, padding=14)
        self.pads_tab = ttk.Frame(self.notebook, padding=14)
        monitoring_tab = ttk.Frame(self.notebook, padding=14)
        settings_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(bindings_tab, text="Effects")
        self.notebook.add(self.pads_tab, text="Test Pad")
        self.notebook.add(monitoring_tab, text="Monitoring")
        self.notebook.add(settings_tab, text="Settings")
        self._build_bindings(bindings_tab)
        self._build_pads(self.pads_tab)
        self._build_monitoring(monitoring_tab)
        self._build_settings(settings_tab)
        self.bind_all("<KeyPress>", self.on_app_keypress, add="+")

    def _build_bindings(self, parent):
        ttk.Label(parent, text="Add another effect to a telemetry trigger, or double-click a row to edit it.").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        ttk.Label(parent, text="Trigger").grid(row=1, column=0, sticky="w")
        ttk.Label(parent, text="Effect").grid(row=1, column=1, sticky="w")
        ttk.Label(parent, text="Strength").grid(row=1, column=2, sticky="w")

        self.trigger_combo = ttk.Combobox(
            parent, textvariable=self.trigger_var, values=self.triggers)
        self.trigger_combo.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        self.trigger_combo.bind("<<ComboboxSelected>>", lambda _: self.update_warning())
        self.trigger_combo.bind("<KeyRelease>", lambda _: self.update_warning())
        self.add_effect_combo = ttk.Combobox(
            parent, textvariable=self.effect_var, values=self.effect_choices, state="readonly")
        self.add_effect_combo.grid(row=2, column=1, sticky="ew", padx=(0, 8))
        ttk.Spinbox(parent, from_=0.0, to=3.0, increment=0.05,
                    textvariable=self.intensity_var, width=8).grid(row=2, column=2, sticky="w")
        ttk.Button(parent, text="Add binding", command=self.add_binding).grid(
            row=2, column=3, padx=(8, 0))
        self.warning_label = ttk.Label(
            parent, textvariable=self.warning_var, style="Special.TLabel")
        self.warning_label.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 10))

        filters = ttk.Frame(parent)
        filters.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Label(filters, text="Filter").pack(side="left")
        search = ttk.Entry(filters, textvariable=self.filter_var, width=28)
        search.pack(side="left", padx=(6, 12))
        search.bind("<KeyRelease>", lambda _: self.refresh_bindings())
        categories = ["All categories"] + sorted(
            {details[0] for details in engine.TRIGGER_METADATA.values()})
        self.category_combo = ttk.Combobox(
            filters, textvariable=self.category_filter_var,
            values=categories, state="readonly", width=18)
        self.category_combo.pack(side="left")
        self.category_combo.bind("<<ComboboxSelected>>", lambda _: self.refresh_bindings())

        self.binding_tree = ttk.Treeview(
            parent, columns=("category", "trigger", "effect", "intensity", "source"),
            show="headings", height=15, selectmode="extended")
        headings = {
            "category": "Category", "trigger": "Telemetry trigger", "effect": "Effect",
            "intensity": "Strength", "source": "Source",
        }
        for column, label in headings.items():
            self.binding_tree.heading(column, text=label,
                                      command=lambda c=column: self.sort_bindings(c))
        self.binding_tree.column("category", width=110)
        self.binding_tree.column("trigger", width=235)
        self.binding_tree.column("effect", width=245)
        self.binding_tree.column("intensity", width=90, anchor="center")
        self.binding_tree.column("source", width=85, anchor="center")
        self.binding_tree.grid(row=5, column=0, columnspan=4, sticky="nsew")
        self.binding_tree.bind("<Double-1>", lambda _: self.edit_selected())
        self.binding_tree.bind("<Button-3>", self.show_binding_context_menu)
        self.binding_tree.bind("<Shift-F10>", self.show_binding_context_menu)
        self.binding_tree.bind("<<TreeviewSelect>>", lambda _: self.show_selected_notice())
        self.binding_tree.tag_configure("special", foreground="#C85B67")

        self.binding_context_menu = tk.Menu(self, tearoff=False)
        self.binding_context_menu.add_command(label="Test", command=self.test_selected)
        self.binding_context_menu.add_command(label="Edit…", command=self.edit_selected)
        self.binding_context_menu.add_separator()
        self.binding_context_menu.add_command(label="Remove", command=self.remove_binding)
        self.binding_context_menu.add_command(label="Reset default", command=self.reset_builtin)

        actions = ttk.Frame(parent)
        actions.grid(row=6, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Button(actions, text="Test selected", command=self.test_selected).pack(side="left")
        ttk.Button(actions, text="Edit selected", command=self.edit_selected).pack(
            side="left", padx=(8, 0))
        ttk.Button(actions, text="Remove selected", command=self.remove_binding).pack(
            side="left", padx=8)
        ttk.Button(actions, text="Reset default", command=self.reset_builtin).pack(side="left")
        ttk.Label(actions, text="Every default mapping is shown; additional mappings are additive.",
                  style="Muted.TLabel").pack(side="left", padx=8)

        parent.columnconfigure(0, weight=2)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(5, weight=1)

    def _build_pads(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Label(toolbar, text="Mode").pack(side="left")
        self.mode_combo = ttk.Combobox(toolbar, textvariable=self.mode_var,
                                       values=sorted(engine.TEST_PAD_MODES), state="readonly", width=20)
        self.mode_combo.pack(side="left", padx=6)
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _: self._load_pads())
        ttk.Button(toolbar, text="New mode", command=self.new_mode).pack(side="left", padx=4)
        ttk.Button(toolbar, text="Delete mode", command=self.delete_mode).pack(side="left")
        ttk.Label(toolbar, text="NumPad 0–9 work while this tab and window have focus.",
                  style="Muted.TLabel").pack(side="right")
        grid = ttk.Frame(parent)
        grid.pack(expand=True)
        positions = {
            7: (0, 0), 8: (0, 1), 9: (0, 2),
            4: (1, 0), 5: (1, 1), 6: (1, 2),
            1: (2, 0), 2: (2, 1), 3: (2, 2),
            0: (3, 1),
        }
        for index, variable in enumerate(self.pad_vars):
            cell = ttk.LabelFrame(grid, text=f"NumPad {index + 1}", padding=10)
            key_number = index
            cell.configure(text=f"NumPad {key_number}")
            row, column = positions[key_number]
            cell.grid(row=row, column=column, padx=8, pady=8, sticky="nsew")
            effect_combo = ttk.Combobox(
                cell, textvariable=variable, values=self.effect_choices,
                state="readonly", width=28)
            effect_combo.pack(fill="x")
            self.pad_effect_combos.append(effect_combo)
            strength = ttk.Frame(cell)
            strength.pack(fill="x", pady=(7, 0))
            ttk.Label(strength, text="Strength").pack(side="left")
            ttk.Spinbox(strength, from_=0.0, to=3.0, increment=0.05,
                        textvariable=self.pad_strength_vars[index], width=7).pack(side="right")
            ttk.Button(cell, text="Play", command=lambda i=index: self.play_pad(i)).pack(
                fill="x", pady=(8, 0))
            variable.trace_add("write", lambda *_: self.save_pads())
            self.pad_strength_vars[index].trace_add("write", lambda *_: self.save_pads())
        for n in range(3):
            grid.columnconfigure(n, weight=1)
        for n in range(4):
            grid.rowconfigure(n, weight=1)

    def _build_settings(self, parent):
        ttk.Label(parent, text="Theme").grid(row=0, column=0, sticky="w", pady=(0, 16))
        self.theme_combo = ttk.Combobox(
            parent, textvariable=self.theme_var, values=list(THEME_OPTIONS.values()),
            state="readonly", width=20)
        self.theme_combo.grid(row=0, column=1, sticky="e", pady=(0, 16))
        self.theme_combo.bind("<<ComboboxSelected>>", lambda _: self.apply_theme())
        ttk.Label(parent, text="Master strength").grid(row=1, column=0, sticky="w")
        ttk.Scale(parent, from_=0.0, to=1.0, variable=self.master_var,
                  command=self.change_master).grid(row=2, column=0, sticky="ew", pady=(4, 16))
        self.master_label = ttk.Label(parent, text=f"{engine.MASTER_VOLUME:.0%}")
        self.master_label.grid(row=2, column=1, padx=10)
        ttk.Label(parent, text="Audio output device").grid(row=3, column=0, sticky="w")
        try:
            devices = [dev["name"] for dev in engine.sd.query_devices()
                       if dev["max_output_channels"] > 0]
        except Exception:
            devices = [engine.OUTPUT_DEVICE_NAME]
        ttk.Combobox(parent, textvariable=self.device_var, values=devices,
                     state="readonly").grid(row=4, column=0, columnspan=2,
                                            sticky="ew", pady=(4, 14))
        ttk.Label(parent, text="Elite Dangerous journal folder").grid(row=5, column=0, sticky="w")
        journal_row = ttk.Frame(parent)
        journal_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 14))
        ttk.Entry(journal_row, textvariable=self.journal_var).pack(side="left", fill="x", expand=True)
        ttk.Button(journal_row, text="Browse…", command=self.browse_journal).pack(
            side="left", padx=(8, 0))
        settings_actions = ttk.Frame(parent)
        settings_actions.grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Button(settings_actions, text="Apply and reconnect", command=self.apply_settings).pack(
            side="left")
        ttk.Button(settings_actions, text="Reset all defaults…", command=self.reset_all_defaults).pack(
            side="left", padx=(8, 0))
        ttk.Label(parent, text="Applying briefly restarts audio and journal monitoring.",
                  style="Muted.TLabel").grid(row=8, column=0, columnspan=2,
                                             sticky="w", pady=(8, 0))
        ttk.Separator(parent).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(20, 14))
        ttk.Label(parent, text="Effect catalog").grid(row=10, column=0, sticky="w")
        effect_actions = ttk.Frame(parent)
        effect_actions.grid(row=11, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(effect_actions, text="Open effects.json",
                   command=self.open_effects_file).pack(side="left")
        ttk.Button(effect_actions, text="Reload effects",
                   command=self.reload_effect_catalog).pack(side="left", padx=(8, 0))
        ttk.Button(effect_actions, text="Authoring guide",
                   command=self.open_effects_guide).pack(side="left", padx=(8, 0))
        ttk.Label(parent, text=engine.EFFECTS_CONFIG_PATH, style="Muted.TLabel",
                  wraplength=720).grid(row=12, column=0, columnspan=2,
                                       sticky="w", pady=(8, 0))
        ttk.Label(parent, text="Trigger catalog").grid(
            row=13, column=0, sticky="w", pady=(18, 0))
        trigger_actions = ttk.Frame(parent)
        trigger_actions.grid(row=14, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(trigger_actions, text="Open triggers.json",
                   command=self.open_triggers_file).pack(side="left")
        ttk.Button(trigger_actions, text="Reload triggers",
                   command=self.reload_trigger_catalog).pack(side="left", padx=(8, 0))
        ttk.Button(trigger_actions, text="Trigger guide",
                   command=self.open_triggers_guide).pack(side="left", padx=(8, 0))
        ttk.Label(parent, text=engine.TRIGGERS_CONFIG_PATH, style="Muted.TLabel",
                  wraplength=720).grid(row=15, column=0, columnspan=2,
                                       sticky="w", pady=(8, 0))
        parent.columnconfigure(0, weight=1)

    def open_effects_file(self):
        try:
            os.startfile(engine.EFFECTS_CONFIG_PATH)
        except (AttributeError, OSError) as exc:
            messagebox.showwarning("Could not open effects.json", str(exc))

    def open_effects_guide(self):
        try:
            os.startfile(EFFECTS_GUIDE_PATH)
        except (AttributeError, OSError) as exc:
            messagebox.showwarning("Could not open authoring guide", str(exc))

    def open_triggers_file(self):
        try:
            os.startfile(engine.TRIGGERS_CONFIG_PATH)
        except (AttributeError, OSError) as exc:
            messagebox.showwarning("Could not open triggers.json", str(exc))

    def open_triggers_guide(self):
        try:
            os.startfile(TRIGGERS_GUIDE_PATH)
        except (AttributeError, OSError) as exc:
            messagebox.showwarning("Could not open trigger guide", str(exc))

    def reload_effect_catalog(self):
        try:
            with open(engine.EFFECTS_CONFIG_PATH, "r", encoding="utf-8") as file:
                catalog = json.load(file)
            definitions = catalog.get("effects")
            if not isinstance(definitions, dict) or not definitions:
                raise ValueError("The catalog must contain a non-empty 'effects' object.")
            for effect_id, definition in definitions.items():
                if not isinstance(definition, dict):
                    raise ValueError(f"Effect '{effect_id}' must be a JSON object.")
                if definition.get("generator") not in ("sequence", "atmospheric_rumble"):
                    raise ValueError(
                        f"Effect '{effect_id}' has an unsupported or missing generator.")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            messagebox.showwarning("Could not reload effects", str(exc))
            return

        engine.EFFECTS_CONFIG_MTIME = None
        engine.load_effect_definitions()
        self.effects = sorted(engine.get_effect_registry())
        self.effect_display_to_id = {
            pretty_effect(effect): effect for effect in self.effects
        }
        self.effect_choices = sorted(self.effect_display_to_id)
        self.add_effect_combo.configure(values=self.effect_choices)
        for combo in self.pad_effect_combos:
            combo.configure(values=self.effect_choices)
        self.refresh_bindings()
        messagebox.showinfo(
            "Effects reloaded",
            f"Loaded {len(engine.EFFECT_DEFINITIONS)} effects from effects.json.")

    def reload_trigger_catalog(self):
        try:
            with open(engine.TRIGGERS_CONFIG_PATH, "r", encoding="utf-8") as file:
                catalog = json.load(file)
            if not isinstance(catalog.get("journal_events"), dict):
                raise ValueError("The catalog must contain a 'journal_events' object.")
            if not isinstance(catalog.get("triggers"), dict) or not catalog["triggers"]:
                raise ValueError("The catalog must contain a non-empty 'triggers' object.")
            if not isinstance(catalog.get("status_flags"), list):
                raise ValueError("The catalog must contain a 'status_flags' array.")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            messagebox.showwarning("Could not reload triggers", str(exc))
            return

        engine.TRIGGERS_CONFIG_MTIME = None
        engine.load_trigger_definitions()
        self.trigger_display_to_id = {
            details[1] + ("*" if details[2] else ""): trigger_id
            for trigger_id, details in engine.TRIGGER_METADATA.items()
        }
        self.trigger_id_to_display = {
            value: key for key, value in self.trigger_display_to_id.items()
        }
        self.triggers = sorted(self.trigger_display_to_id)
        self.trigger_combo.configure(values=self.triggers)
        categories = ["All categories"] + sorted(
            {details[0] for details in engine.TRIGGER_METADATA.values()})
        self.category_combo.configure(values=categories)
        if self.category_filter_var.get() not in categories:
            self.category_filter_var.set("All categories")
        self.refresh_bindings()
        messagebox.showinfo(
            "Triggers reloaded",
            f"Loaded {len(engine.TRIGGER_METADATA)} triggers from triggers.json.")

    def _build_monitoring(self, parent):
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="Events and haptic usage for this application session.",
                  style="Muted.TLabel").pack(side="left")
        ttk.Button(toolbar, text="Clear session", command=self.clear_monitoring).pack(side="right")

        summary_frame = ttk.LabelFrame(parent, text="Session totals", padding=8)
        summary_frame.pack(fill="both", pady=(0, 10))
        self.monitor_summary = ttk.Treeview(
            summary_frame, columns=("source", "event", "count"), show="headings", height=7)
        self.monitor_summary.heading("source", text="Source")
        self.monitor_summary.heading("event", text="Event or effect")
        self.monitor_summary.heading("count", text="Count")
        self.monitor_summary.column("source", width=115, stretch=False)
        self.monitor_summary.column("event", width=420)
        self.monitor_summary.column("count", width=80, anchor="center", stretch=False)
        summary_scroll = ttk.Scrollbar(
            summary_frame, orient="vertical", command=self.monitor_summary.yview)
        self.monitor_summary.configure(yscrollcommand=summary_scroll.set)
        self.monitor_summary.pack(side="left", fill="both", expand=True)
        summary_scroll.pack(side="right", fill="y")

        log_frame = ttk.LabelFrame(parent, text="Recent activity", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.monitor_log = ttk.Treeview(
            log_frame, columns=("time", "source", "event", "details"),
            show="headings", height=12)
        for column, label in (("time", "Time"), ("source", "Source"),
                              ("event", "Event or effect"), ("details", "Details")):
            self.monitor_log.heading(column, text=label)
        self.monitor_log.column("time", width=85, anchor="center", stretch=False)
        self.monitor_log.column("source", width=105, stretch=False)
        self.monitor_log.column("event", width=260)
        self.monitor_log.column("details", width=300)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.monitor_log.yview)
        self.monitor_log.configure(yscrollcommand=log_scroll.set)
        self.monitor_log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def poll_monitor_events(self):
        processed = 0
        while processed < 250:
            try:
                event = self.monitor_queue.get_nowait()
            except queue.Empty:
                break
            source = event.get("source", "App")
            raw_name = event.get("name", "")
            details = event.get("details", "")
            display_name = raw_name
            if source in ("App trigger", "Status"):
                display_name = trigger_details(raw_name)[1]
                details = details or raw_name

            key = (source, display_name)
            self.monitor_counts[key] = self.monitor_counts.get(key, 0) + 1
            item = self.monitor_count_items.get(key)
            values = (source, display_name, self.monitor_counts[key])
            if item:
                self.monitor_summary.item(item, values=values)
            else:
                self.monitor_count_items[key] = self.monitor_summary.insert(
                    "", "end", values=values)

            self.monitor_log.insert(
                "", 0, values=(time.strftime("%H:%M:%S"), source, display_name, details))
            children = self.monitor_log.get_children()
            if len(children) > 1000:
                self.monitor_log.delete(*children[1000:])
            processed += 1
        self.after(200, self.poll_monitor_events)

    def clear_monitoring(self):
        self.monitor_counts.clear()
        self.monitor_count_items.clear()
        self.monitor_summary.delete(*self.monitor_summary.get_children())
        self.monitor_log.delete(*self.monitor_log.get_children())
        while True:
            try:
                self.monitor_queue.get_nowait()
            except queue.Empty:
                break

    def apply_theme(self, save=True):
        theme_id = normalize_theme_id(self.theme_var.get())
        engine.COLOR_THEME = theme_id
        if theme_id == "dark_blue":
            palette = {
                "background": "#101010", "surface": "#102033", "field": "#0D1825",
                "foreground": "#DFEAF2", "muted": "#8AA7BD", "border": "#263B50",
                "select": "#162430", "accent": "#72B7F2", "accent_hover": "#93CAFA",
                "hover": "#18334F", "hover_foreground": "#FFFFFF",
                "trough": "#182839", "special": "#E47A86",
            }
        elif theme_id == "elite_amber":
            palette = {
                "background": "#000000", "surface": "#1A0D00", "field": "#0D0700",
                "foreground": "#FFE3C2", "muted": "#C58A55", "border": "#5A2B00",
                "select": "#3A1900", "accent": "#FF7100", "accent_hover": "#FF9A3D",
                "hover": "#3D1E00", "hover_foreground": "#FFFFFF",
                "trough": "#211000", "special": "#E47A86",
            }
        else:
            palette = {
                "background": "#F3F5F7", "surface": "#FFFFFF", "field": "#FFFFFF",
                "foreground": "#20272C", "muted": "#647583", "border": "#C5CED6",
                "select": "#DCEAF7", "accent": "#3478B8", "accent_hover": "#24679F",
                "hover": "#E1E7ED", "hover_foreground": "#20272C",
                "trough": "#D4DCE3", "special": "#C85B67",
            }
        self.style.theme_use("clam")
        self.configure(background=palette["background"])
        self.style.configure(".", background=palette["background"],
                             foreground=palette["foreground"], bordercolor=palette["border"],
                             lightcolor=palette["border"], darkcolor=palette["border"],
                             font=("Segoe UI", 10))
        for style_name in ("TFrame", "TLabel"):
            self.style.configure(style_name, background=palette["background"],
                                 foreground=palette["foreground"])
        for style_name in ("TCheckbutton", "TRadiobutton"):
            self.style.configure(style_name, background=palette["background"],
                                 foreground=palette["foreground"],
                                 indicatorbackground=palette["field"],
                                 indicatorforeground=palette["accent"])
            self.style.map(style_name,
                           background=[("active", palette["hover"])],
                           foreground=[("active", palette["hover_foreground"])],
                           indicatorbackground=[("active", palette["hover"])])
        self.style.configure("TLabelframe", background=palette["background"],
                             foreground=palette["muted"], bordercolor=palette["border"],
                             relief="flat")
        self.style.configure("TLabelframe.Label", background=palette["background"],
                             foreground=palette["muted"], font=("Segoe UI Semibold", 9))
        self.style.configure("Titlebar.TFrame", background=palette["surface"])
        self.style.configure("Title.TLabel", background=palette["surface"],
                             foreground=palette["accent"],
                             font=("Segoe UI Semibold", 11))
        self.style.configure("HeaderStatus.TLabel", background=palette["surface"],
                             foreground=palette["muted"], font=("Segoe UI", 9))
        self.style.configure("TNotebook", background=palette["background"], borderwidth=0)
        self.style.configure("TNotebook.Tab", background=palette["surface"],
                             foreground=palette["foreground"], padding=(12, 8),
                             borderwidth=0, relief="flat")
        self.style.map("TNotebook.Tab",
                       background=[("selected", palette["accent"]),
                                   ("active", palette["hover"])],
                       foreground=[("selected", palette["background"]),
                                   ("active", palette["hover_foreground"])],
                       padding=[("selected", (12, 8)), ("!selected", (12, 8))],
                       borderwidth=[("selected", 0), ("!selected", 0)],
                       relief=[("selected", "flat"), ("!selected", "flat")])
        self.style.configure("TButton", background=palette["surface"],
                             foreground=palette["foreground"], padding=(12, 7),
                             borderwidth=0, relief="flat")
        self.style.map("TButton", background=[("pressed", palette["select"]),
                                               ("active", palette["hover"])],
                       foreground=[("pressed", palette["accent"]),
                                   ("active", palette["hover_foreground"])])
        self.style.configure("TScale", background=palette["background"],
                             troughcolor=palette["trough"],
                             bordercolor=palette["border"],
                             lightcolor=palette["accent"], darkcolor=palette["accent"],
                             sliderlength=24)
        self.style.map("TScale", background=[("active", palette["accent_hover"]),
                                             ("!disabled", palette["accent"])],
                       lightcolor=[("active", palette["accent_hover"])],
                       darkcolor=[("active", palette["accent_hover"])])
        for style_name in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar",
                           "TCombobox.Vertical.TScrollbar"):
            self.style.configure(style_name, background=palette["accent"],
                                 troughcolor=palette["trough"],
                                 bordercolor=palette["border"],
                                 lightcolor=palette["accent"], darkcolor=palette["accent"],
                                 arrowcolor=palette["hover_foreground"], relief="flat")
            self.style.map(style_name,
                           background=[("active", palette["accent_hover"]),
                                       ("!disabled", palette["accent"])],
                           lightcolor=[("active", palette["accent_hover"])],
                           darkcolor=[("active", palette["accent_hover"])])
        for style_name in ("TEntry", "TCombobox", "TSpinbox"):
            self.style.configure(style_name, fieldbackground=palette["field"],
                                 background=palette["surface"], foreground=palette["foreground"],
                                 insertcolor=palette["foreground"], arrowcolor=palette["foreground"])
            self.style.map(style_name,
                           fieldbackground=[("readonly", palette["field"])],
                           background=[("active", palette["hover"])],
                           foreground=[("readonly", palette["foreground"])],
                           arrowcolor=[("active", palette["hover_foreground"])])
        self.style.configure("Treeview", background=palette["field"],
                             fieldbackground=palette["field"], foreground=palette["foreground"],
                             rowheight=29, borderwidth=0, relief="flat")
        self.style.configure("Treeview.Heading", background=palette["surface"],
                             foreground=palette["muted"], padding=(8, 7),
                             borderwidth=0, relief="flat",
                             font=("Segoe UI Semibold", 9))
        self.style.map("Treeview.Heading",
                       background=[("pressed", palette["hover"]),
                                   ("active", palette["hover"])],
                       foreground=[("pressed", palette["hover_foreground"]),
                                   ("active", palette["hover_foreground"])])
        self.style.map("Treeview", background=[("selected", palette["select"])],
                       foreground=[("selected", palette["accent"])])
        self.style.configure("Muted.TLabel", background=palette["background"],
                             foreground=palette["muted"])
        self.style.configure("Special.TLabel", background=palette["background"],
                             foreground=palette["special"])
        self.style.configure("Status.TLabel", background=palette["background"],
                             foreground=palette["muted"])
        self.option_add("*TCombobox*Listbox.background", palette["field"])
        self.option_add("*TCombobox*Listbox.foreground", palette["foreground"])
        self.option_add("*TCombobox*Listbox.selectBackground", palette["select"])
        if hasattr(self, "binding_tree"):
            self.binding_tree.tag_configure("special", foreground=palette["special"])
        if hasattr(self, "binding_context_menu"):
            self.binding_context_menu.configure(background=palette["surface"],
                                                foreground=palette["foreground"],
                                                activebackground=palette["hover"],
                                                activeforeground=palette["hover_foreground"])
        if save:
            engine.save_config()

    def browse_journal(self):
        selected = filedialog.askdirectory(initialdir=self.journal_var.get(),
                                           title="Select Elite Dangerous journal folder")
        if selected:
            self.journal_var.set(selected)

    def apply_settings(self):
        journal = self.journal_var.get().strip()
        device = self.device_var.get().strip()
        if not os.path.isdir(journal):
            messagebox.showwarning("Journal folder", "Choose an existing journal folder.")
            return
        engine.OUTPUT_DEVICE_NAME = device
        engine.JOURNAL_DIR = journal
        engine.save_config()
        try:
            self.runtime.stop()
            self.runtime = Runtime()
            self.runtime.start()
            self.status_text.set("● Monitoring Elite Dangerous")
        except Exception as exc:
            self.status_text.set("● Setup needs attention")
            messagebox.showwarning("Could not reconnect", str(exc))

    def reset_all_defaults(self):
        if not messagebox.askyesno(
                "Reset all defaults",
                "Reset settings, effect mappings, added bindings, and Test Pad modes?"):
            return
        engine.load_default_config()
        defaults = engine.DEFAULT_CONFIG
        settings = defaults.get("settings", {})
        engine.MASTER_VOLUME = float(settings.get("master_volume", 0.25))
        engine.OUTPUT_DEVICE_NAME = settings.get("output_device", "Voicemeeter Input")
        engine.JOURNAL_DIR = os.path.expandvars(settings.get("journal_directory", engine.JOURNAL_DIR))
        engine.COLOR_THEME = settings.get("theme", "dark_blue")
        engine.WINDOW_GEOMETRY = settings.get("window_geometry", "940x780")
        engine.CUSTOM_BINDINGS = list(defaults.get("custom_bindings", []))
        engine.BUILTIN_OVERRIDES = {}
        engine.TEST_PAD_MODES = dict(defaults.get("test_pad_modes", {"Default": {}}))
        self.master_var.set(engine.MASTER_VOLUME)
        self.master_label.configure(text=f"{engine.MASTER_VOLUME:.0%}")
        self.device_var.set(engine.OUTPUT_DEVICE_NAME)
        self.journal_var.set(engine.JOURNAL_DIR)
        theme_id = normalize_theme_id(engine.COLOR_THEME)
        self.theme_var.set(THEME_OPTIONS[theme_id])
        self.apply_theme(save=False)
        self.geometry(engine.WINDOW_GEOMETRY)
        self.mode_combo.configure(values=sorted(engine.TEST_PAD_MODES))
        self.mode_var.set(sorted(engine.TEST_PAD_MODES)[0])
        self._load_pads()
        self.refresh_bindings()
        engine.save_config()
        self.apply_settings()

    def start_runtime(self):
        try:
            self.runtime.start()
            self.status_text.set("● Monitoring Elite Dangerous")
        except Exception as exc:
            self.status_text.set("● Setup needs attention")
            messagebox.showwarning("Could not start monitoring", str(exc))

    def update_warning(self):
        selected = self.trigger_var.get().strip()
        trigger = self.trigger_display_to_id.get(selected, selected)
        count = sum(1 for b in engine.CUSTOM_BINDINGS if b.get("trigger") == trigger)
        built_in = any(row.get("trigger") == trigger for row in engine.get_builtin_bindings())
        if built_in or count:
            self.warning_label.configure(style="Special.TLabel")
            parts = []
            if built_in:
                parts.append("a default mapping")
            if count:
                parts.append(f"{count} custom binding{'s' if count != 1 else ''}")
            self.warning_var.set("This trigger already has " + " and ".join(parts) +
                                 ". Multiple effects can be added for one trigger.")
        else:
            self.warning_var.set("")

    def add_binding(self):
        selected_trigger = self.trigger_var.get().strip()
        trigger = self.trigger_display_to_id.get(selected_trigger, selected_trigger)
        effect = self.effect_display_to_id.get(self.effect_var.get().strip(), "")
        if not trigger or not effect:
            messagebox.showinfo("Choose a binding", "Choose both a telemetry trigger and an effect.")
            return
        engine.CUSTOM_BINDINGS.append({
            "trigger": trigger,
            "effect": effect,
            "intensity": round(float(self.intensity_var.get()), 2),
            "enabled": True,
        })
        engine.save_config()
        self.refresh_bindings()
        self.update_warning()

    def refresh_bindings(self):
        for item in self.binding_tree.get_children():
            self.binding_tree.delete(item)
        rows = []
        for index, binding in enumerate(engine.get_builtin_bindings()):
            rows.append((f"builtin:{index}", binding))
        for index, binding in enumerate(engine.CUSTOM_BINDINGS):
            row = dict(binding)
            row["source"] = "Custom"
            row["control"] = False
            rows.append((f"custom:{index}", row))

        needle = self.filter_var.get().strip().lower()
        category_filter = self.category_filter_var.get()
        decorated = []
        for iid, binding in rows:
            effect_id = binding.get("effect", "")
            category, trigger_label, special, special_note = trigger_details(
                binding.get("trigger", ""))
            effect_label = pretty_effect(effect_id)
            if special:
                trigger_label += "*"
            values = (category, trigger_label, effect_label,
                      ("Automatic" if binding.get("action") == "continuous_stop"
                       else f"{float(binding.get('intensity', 1.0)):.2f}"),
                      binding.get("source", "Custom"))
            if category_filter != "All categories" and category != category_filter:
                continue
            if needle and needle not in " ".join(values).lower():
                continue
            decorated.append((iid, binding, values, special))

        column_index = {"category": 0, "trigger": 1, "effect": 2,
                        "intensity": 3, "source": 4}[self.sort_column]
        decorated.sort(key=lambda row: str(row[2][column_index]).lower(), reverse=self.sort_reverse)
        for iid, _, values, special in decorated:
            self.binding_tree.insert("", "end", iid=iid, values=values,
                                     tags=("special",) if special is True else ())

    def show_selected_notice(self):
        _, _, binding = self._selected_binding()
        if not binding:
            return
        _, _, special, metadata_note = trigger_details(binding.get("trigger", ""))
        note = binding.get("description", metadata_note)
        if special is True:
            self.warning_label.configure(style="Special.TLabel")
            self.warning_var.set("* Special trigger: " + note)
        elif special == "advanced":
            self.warning_label.configure(style="TLabel")
            self.warning_var.set("* Advanced Maintenance: " + note)
        else:
            self.warning_var.set("")

    def sort_bindings(self, column):
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.refresh_bindings()

    def _selected_binding(self):
        selected = self.binding_tree.selection()
        if not selected:
            return None, None, None
        iid = selected[0]
        kind, raw_index = iid.split(":", 1)
        index = int(raw_index)
        if kind == "builtin":
            bindings = engine.get_builtin_bindings()
            return kind, index, bindings[index] if index < len(bindings) else None
        return kind, index, engine.CUSTOM_BINDINGS[index] if index < len(engine.CUSTOM_BINDINGS) else None

    def show_binding_context_menu(self, event):
        if getattr(event, "keysym", "") == "F10":
            selected = self.binding_tree.selection()
            if not selected:
                return "break"
            row_box = self.binding_tree.bbox(selected[0])
            x = self.binding_tree.winfo_rootx() + (row_box[0] if row_box else 0) + 24
            y = self.binding_tree.winfo_rooty() + (row_box[1] if row_box else 0) + 24
        else:
            row = self.binding_tree.identify_row(event.y)
            if not row:
                return "break"
            if row not in self.binding_tree.selection():
                self.binding_tree.selection_set(row)
                self.binding_tree.focus(row)
            x, y = event.x_root, event.y_root

        kind, _, binding = self._selected_binding()
        testable = bool(binding and not binding.get("control"))
        editable = bool(binding and binding.get("action") != "continuous_stop")
        self.binding_context_menu.entryconfigure("Test", state="normal" if testable else "disabled")
        self.binding_context_menu.entryconfigure("Edit…", state="normal" if editable else "disabled")
        self.binding_context_menu.entryconfigure("Remove", state="normal" if kind == "custom" else "disabled")
        self.binding_context_menu.entryconfigure(
            "Reset default", state="normal" if kind == "builtin" else "disabled")
        try:
            self.binding_context_menu.tk_popup(x, y)
        finally:
            self.binding_context_menu.grab_release()
        return "break"

    def center_dialog(self, dialog):
        """Center a completed dialog over the main application window."""
        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        dialog.geometry(f"+{x}+{y}")

    def bind_dialog_shortcuts(self, dialog, default_command, cancel_command=None):
        """Give modal dialogs conventional default and cancel keyboard actions."""
        cancel_command = cancel_command or dialog.destroy

        def invoke_default(_event=None):
            default_command()
            return "break"

        def invoke_cancel(_event=None):
            cancel_command()
            return "break"

        dialog.bind("<Return>", invoke_default)
        dialog.bind("<KP_Enter>", invoke_default)
        dialog.bind("<Escape>", invoke_cancel)

    def edit_selected(self):
        kind, index, binding = self._selected_binding()
        if not binding:
            return
        locked_stop = binding.get("action") == "continuous_stop"
        if locked_stop:
            messagebox.showinfo("Automatic stop",
                                "This stop action is always automatic so a continuous effect cannot "
                                "be left running. Add another mapping to this trigger for extra feedback.")
            return

        dialog = tk.Toplevel(self)
        dialog.title("Edit effect mapping")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        effect_var = tk.StringVar(value=pretty_effect(binding.get("effect", "")))
        intensity_var = tk.DoubleVar(value=float(binding.get("intensity", 1.0)))
        enabled_var = tk.BooleanVar(value=binding.get("enabled", True))
        ttk.Label(frame, text=binding.get("trigger", ""), font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(frame, text="Effect").grid(row=1, column=0, sticky="w")
        effect_values = (self.effect_choices if not binding.get("control")
                         else [pretty_effect(binding.get("effect", ""))])
        ttk.Combobox(frame, textvariable=effect_var, values=effect_values,
                     state="readonly", width=34).grid(row=1, column=1, padx=(10, 0))
        ttk.Label(frame, text="Strength").grid(row=2, column=0, sticky="w", pady=10)
        ttk.Spinbox(frame, from_=0.0, to=3.0, increment=0.05,
                    textvariable=intensity_var, width=10).grid(row=2, column=1, sticky="w", padx=(10, 0))
        ttk.Checkbutton(frame, text="Enabled", variable=enabled_var).grid(
            row=3, column=0, columnspan=2, sticky="w")

        def save():
            effect_id = self.effect_display_to_id.get(effect_var.get(), binding.get("effect", ""))
            updated = {"effect": effect_id, "intensity": round(float(intensity_var.get()), 2),
                       "enabled": bool(enabled_var.get())}
            if kind == "builtin":
                engine.BUILTIN_OVERRIDES[binding["trigger"]] = updated
            else:
                engine.CUSTOM_BINDINGS[index].update(updated)
            engine.save_config()
            self.refresh_bindings()
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left")
        ttk.Button(buttons, text="Save", command=save).pack(side="left", padx=(8, 0))
        self.bind_dialog_shortcuts(dialog, save)
        self.center_dialog(dialog)

    def remove_binding(self):
        selected = self.binding_tree.selection()
        if not selected:
            return
        custom_indices = sorted(
            (int(item.split(":", 1)[1]) for item in selected if item.startswith("custom:")),
            reverse=True)
        for index in custom_indices:
            del engine.CUSTOM_BINDINGS[index]
        engine.save_config()
        self.refresh_bindings()
        self.update_warning()

    def test_selected(self):
        _, _, binding = self._selected_binding()
        if not binding or binding.get("control"):
            return
        effect = engine.get_effect_registry().get(binding.get("effect"))
        if effect:
            engine.interrupt_playback()
            engine.queue_haptic(effect, binding.get("intensity", 1.0))

    def reset_builtin(self):
        kind, _, binding = self._selected_binding()
        if kind == "builtin" and binding:
            engine.BUILTIN_OVERRIDES.pop(binding.get("trigger"), None)
            engine.save_config()
            self.refresh_bindings()

    def _load_pads(self):
        self.loading_pads = True
        self.active_mode_name = self.mode_var.get()
        mode = engine.TEST_PAD_MODES.get(self.mode_var.get(), {})
        for index, variable in enumerate(self.pad_vars):
            assignment = mode.get(str(index), {})
            variable.set(pretty_effect(assignment.get("effect", "")))
            self.pad_strength_vars[index].set(float(assignment.get("intensity", 1.0)))
        self.loading_pads = False

    def save_pads(self):
        if self.loading_pads:
            return
        mode_name = self.mode_var.get()
        assignments = {}
        for index, variable in enumerate(self.pad_vars):
            if not variable.get():
                continue
            try:
                intensity = round(float(self.pad_strength_vars[index].get()), 2)
            except (TypeError, ValueError, tk.TclError):
                intensity = 1.0
            assignments[str(index)] = {
                "effect": self.effect_display_to_id.get(variable.get(), ""), "intensity": intensity,
            }
        engine.TEST_PAD_MODES[mode_name] = {
            **assignments
        }
        engine.save_config()

    def play_pad(self, index):
        effect_id = self.effect_display_to_id.get(self.pad_vars[index].get(), "")
        effect = engine.get_effect_registry().get(effect_id)
        if effect:
            engine.interrupt_playback()
            engine.queue_haptic(effect, self.pad_strength_vars[index].get())

    def new_mode(self):
        dialog = tk.Toplevel(self)
        dialog.title("New test mode")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.pack()
        name_var = tk.StringVar(value="New mode")
        ttk.Label(frame, text="Mode name").pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=name_var, width=28)
        entry.pack(fill="x", pady=8)
        entry.select_range(0, "end")
        entry.focus_set()

        def create():
            name = name_var.get().strip()
            if not name or name in engine.TEST_PAD_MODES:
                messagebox.showwarning("Mode name", "Enter a unique mode name.", parent=dialog)
                return
            engine.TEST_PAD_MODES[name] = {}
            self.mode_combo.configure(values=sorted(engine.TEST_PAD_MODES))
            self.mode_var.set(name)
            self._load_pads()
            engine.save_config()
            dialog.destroy()

        ttk.Button(frame, text="Create", command=create).pack(anchor="e")
        self.bind_dialog_shortcuts(dialog, create)
        self.center_dialog(dialog)

    def delete_mode(self):
        name = self.mode_var.get()
        if len(engine.TEST_PAD_MODES) <= 1:
            messagebox.showinfo("Test modes", "At least one test mode is required.")
            return
        engine.TEST_PAD_MODES.pop(name, None)
        self.mode_combo.configure(values=sorted(engine.TEST_PAD_MODES))
        self.mode_var.set(sorted(engine.TEST_PAD_MODES)[0])
        self._load_pads()
        engine.save_config()

    def on_app_keypress(self, event):
        if self.notebook.select() != str(self.pads_tab):
            return
        keys = {
            "KP_Insert": 0, "KP_0": 0,
            "KP_End": 1, "KP_1": 1, "KP_Down": 2, "KP_2": 2,
            "KP_Next": 3, "KP_3": 3, "KP_Left": 4, "KP_4": 4,
            "KP_Begin": 5, "KP_5": 5, "KP_Right": 6, "KP_6": 6,
            "KP_Home": 7, "KP_7": 7, "KP_Up": 8, "KP_8": 8,
            "KP_Prior": 9, "KP_9": 9,
        }
        # Windows reports NumPad 0-9 as virtual-key codes 96-105. Keep the
        # keysym fallbacks for Num Lock variations and other Tk builds.
        index = event.keycode - 96 if 96 <= event.keycode <= 105 else keys.get(event.keysym)
        if index is not None:
            self.play_pad(index)
            return "break"

    def change_master(self, _=None):
        engine.MASTER_VOLUME = round(float(self.master_var.get()), 2)
        self.master_label.configure(text=f"{engine.MASTER_VOLUME:.0%}")
        engine.save_config()

    def on_window_configure(self, event):
        if event.widget is not self or self.state() != "normal":
            return
        if self._geometry_save_job is not None:
            self.after_cancel(self._geometry_save_job)
        self._geometry_save_job = self.after(500, self.persist_window_geometry)

    def persist_window_geometry(self):
        self._geometry_save_job = None
        if self.state() == "normal":
            engine.WINDOW_GEOMETRY = self.geometry()
            engine.save_config()

    def close(self):
        engine.remove_monitor_listener(self.monitor_queue.put)
        if self._geometry_save_job is not None:
            self.after_cancel(self._geometry_save_job)
            self._geometry_save_job = None
        if self.state() == "normal":
            engine.WINDOW_GEOMETRY = self.geometry()
        engine.save_config()
        self.runtime.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
