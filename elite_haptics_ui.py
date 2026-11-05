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
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from effect_recorder import (append_gesture, effect_definition, pad_values,
                             trim_silence, unique_effect_id, validate_sequence_steps)


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
APP_ICON_PATH = os.path.join(HERE, "elite-haptics.ico")
CHANGELOG_CONFIG_PATH = os.path.join(HERE, "changelog.json")

THEME_OPTIONS = {
    "dark_blue": "Dark Blue",
    "light": "Light Blue",
    "elite_amber": "Elite",
}
THEME_IDS_BY_LABEL = {label: theme_id for theme_id, label in THEME_OPTIONS.items()}

WINDOW_GLYPHS = {
    "minimize": "\ue921",
    "maximize": "\ue922",
    "restore": "\ue923",
    "close": "\ue8bb",
}


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
    external_defaults_path = os.path.join(application_dir, "effects.default.json")
    external_effects_path = os.path.join(application_dir, "effects.json")
    external_manifest_path = os.path.join(application_dir, "effects.legacy-manifest.json")
    external_migrations_path = os.path.join(application_dir, "effect_migrations.json")
    external_triggers_path = os.path.join(application_dir, "triggers.json")
    external_guide_path = os.path.join(application_dir, "EFFECTS_GUIDE.md")
    external_triggers_guide_path = os.path.join(application_dir, "TRIGGERS_GUIDE.md")
    try:
        for bundled, external in (
                (engine.EFFECTS_DEFAULT_PATH, external_defaults_path),
                (engine.EFFECTS_LEGACY_MANIFEST_PATH, external_manifest_path),
                (engine.EFFECT_MIGRATIONS_PATH, external_migrations_path)):
            if not os.path.exists(external) and os.path.exists(bundled):
                shutil.copyfile(bundled, external)
        if not os.path.exists(external_triggers_path):
            shutil.copyfile(engine.TRIGGERS_CONFIG_PATH, external_triggers_path)
        if not os.path.exists(external_guide_path) and os.path.exists(EFFECTS_GUIDE_PATH):
            shutil.copyfile(EFFECTS_GUIDE_PATH, external_guide_path)
        if (not os.path.exists(external_triggers_guide_path)
                and os.path.exists(TRIGGERS_GUIDE_PATH)):
            shutil.copyfile(TRIGGERS_GUIDE_PATH, external_triggers_guide_path)
        engine.EFFECTS_CONFIG_PATH = external_effects_path
        engine.EFFECTS_DEFAULT_PATH = external_defaults_path
        engine.EFFECTS_LEGACY_MANIFEST_PATH = external_manifest_path
        engine.EFFECT_MIGRATIONS_PATH = external_migrations_path
        engine.prepare_effect_catalog()
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


def load_user_effect_catalog():
    with open(engine.EFFECTS_CONFIG_PATH, "r", encoding="utf-8") as file:
        catalog = json.load(file)
    if not isinstance(catalog.get("effects"), dict):
        raise ValueError("effects.json must contain an 'effects' object.")
    return catalog


def write_user_effect_catalog(catalog):
    temporary = engine.EFFECTS_CONFIG_PATH + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(catalog, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(temporary, engine.EFFECTS_CONFIG_PATH)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def duplicate_effect_name(name, exclude_id=None):
    wanted = name.strip().casefold()
    for effect_id, definition in engine.EFFECT_DEFINITIONS.items():
        if effect_id != exclude_id and str(definition.get("name", "")).strip().casefold() == wanted:
            return effect_id, definition.get("name", effect_id)
    return None


def is_builtin_effect_id(effect_id):
    try:
        with open(engine.EFFECTS_DEFAULT_PATH, "r", encoding="utf-8") as file:
            return effect_id in json.load(file).get("effects", {})
    except (OSError, json.JSONDecodeError):
        return False


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


class EffectRecorderDialog(tk.Toplevel):
    """Modal gesture recorder that writes custom sequence effects."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.title("Record custom effect")
        self.transient(owner)
        self.resizable(False, False)
        self.configure(background=owner.palette["background"])
        if os.path.exists(APP_ICON_PATH):
            try:
                self.iconbitmap(APP_ICON_PATH)
            except tk.TclError:
                pass
        self.steps = []
        self.recording = False
        self.press = None
        self.previous_release = None
        self.live_name = f"effect_recorder_{id(self)}"
        self.name_var = tk.StringVar(value="My Custom Effect")
        self.category_var = tk.StringVar(value="Custom")
        self.description_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Press Start, then play the pad.")
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        owner.bind_dialog_shortcuts(self, self.save, self.cancel)
        self.grab_set()
        owner.center_dialog(self)
        self.name_entry.focus_set()

    def _build(self):
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Effect Recorder", style="Title.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(body, text="Move the crosshair: vertical changes frequency • horizontal changes intensity",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=4,
                                              sticky="w", pady=(3, 12))
        pad_frame = ttk.Frame(body)
        pad_frame.grid(row=2, column=0, columnspan=4)
        freq_guide = ttk.Frame(pad_frame, width=116, height=260)
        freq_guide.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        freq_guide.grid_propagate(False)
        ttk.Label(freq_guide, text="HIGH FREQUENCY\n120 Hz", style="Muted.TLabel",
                  justify="right").pack(anchor="ne")
        ttk.Label(freq_guide, text="LOW FREQUENCY\n20 Hz", style="Muted.TLabel",
                  justify="right").pack(side="bottom", anchor="se")
        self.pad = tk.Canvas(pad_frame, width=520, height=260, highlightthickness=1,
                             highlightbackground=self.owner.palette["border"],
                             background=self.owner.palette["field"], cursor="crosshair")
        self.pad.grid(row=0, column=1)
        self.pad.create_text(260, 130, text="CLICK / HOLD / DRAG",
                             fill=self.owner.palette["muted"], font=("Segoe UI Semibold", 13))
        strength_guide = ttk.Frame(pad_frame)
        strength_guide.grid(row=1, column=1, sticky="ew", pady=(5, 0))
        ttk.Label(strength_guide, text="SOFT  10%", style="Muted.TLabel").pack(side="left")
        ttk.Label(strength_guide, text="INTENSITY", style="Muted.TLabel").pack(
            side="left", expand=True)
        ttk.Label(strength_guide, text="STRONG  100%", style="Muted.TLabel").pack(side="right")
        self.cross_x = self.pad.create_line(260, 0, 260, 260,
                                            fill=self.owner.palette["accent"], dash=(3, 4))
        self.cross_y = self.pad.create_line(0, 130, 520, 130,
                                            fill=self.owner.palette["accent"], dash=(3, 4))
        self.cross_dot = self.pad.create_oval(255, 125, 265, 135,
                                              outline=self.owner.palette["accent"], width=2)
        self.pad_readout = self.pad.create_text(
            272, 118, text="70 Hz  •  55%", anchor="sw",
            fill=self.owner.palette["accent"], font=("Segoe UI Semibold", 9))
        self.pad.bind("<Motion>", self.pad_motion)
        self.pad.bind("<ButtonPress-1>", self.pad_press)
        self.pad.bind("<B1-Motion>", self.pad_drag)
        self.pad.bind("<ButtonRelease-1>", self.pad_release)

        controls = ttk.Frame(body)
        controls.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(12, 16))
        ttk.Button(controls, text="Record", command=self.start).pack(side="left")
        ttk.Button(controls, text="Stop", command=self.stop).pack(side="left", padx=(7, 0))
        ttk.Button(controls, text="Clear", command=self.clear).pack(side="left", padx=(7, 0))
        ttk.Button(controls, text="Preview", command=self.preview).pack(side="left", padx=(7, 0))
        ttk.Button(controls, text="Trim Silence", command=self.trim).pack(side="left", padx=(7, 0))
        ttk.Label(controls, textvariable=self.status_var, style="Muted.TLabel").pack(
            side="right", padx=(16, 0))

        ttk.Label(body, text="Name").grid(row=4, column=0, sticky="w")
        self.name_entry = ttk.Entry(body, textvariable=self.name_var, width=34)
        self.name_entry.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(3, 10))
        ttk.Label(body, text="Category").grid(row=4, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(body, textvariable=self.category_var, width=20).grid(
            row=5, column=2, columnspan=2, sticky="ew", padx=(12, 0), pady=(3, 10))
        ttk.Label(body, text="Description").grid(row=6, column=0, sticky="w")
        ttk.Entry(body, textvariable=self.description_var).grid(
            row=7, column=0, columnspan=4, sticky="ew", pady=(3, 16))
        actions = ttk.Frame(body)
        actions.grid(row=8, column=0, columnspan=4, sticky="e")
        ttk.Button(actions, text="Cancel", command=self.cancel).pack(side="left")
        ttk.Button(actions, text="Save effect", command=self.save).pack(
            side="left", padx=(8, 0))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        body.columnconfigure(3, weight=1)

    def _values(self, event):
        return pad_values(event.x, event.y, int(self.pad["width"]), int(self.pad["height"]))

    def pad_motion(self, event):
        width, height = int(self.pad["width"]), int(self.pad["height"])
        x = min(width, max(0, event.x))
        y = min(height, max(0, event.y))
        frequency, strength = pad_values(x, y, width, height)
        self.pad.coords(self.cross_x, x, 0, x, height)
        self.pad.coords(self.cross_y, 0, y, width, y)
        self.pad.coords(self.cross_dot, x - 5, y - 5, x + 5, y + 5)
        anchor = "se" if x > width - 115 else "sw"
        label_x = x - 10 if anchor == "se" else x + 10
        label_y = y - 8 if y > 28 else y + 22
        self.pad.coords(self.pad_readout, label_x, label_y)
        self.pad.itemconfigure(self.pad_readout, anchor=anchor,
                               text=f"{frequency:.0f} Hz  •  {strength:.0%}")

    def start(self):
        self.recording = True
        self.previous_release = time.monotonic()
        self.status_var.set("Recording…")

    def stop(self):
        self.recording = False
        self._stop_live()
        self.press = None
        self.status_var.set(f"Stopped • {len(self.steps)} steps")

    def clear(self):
        self.stop()
        self.steps = []
        self.previous_release = None
        self.status_var.set("Cleared")

    def trim(self):
        self.steps = trim_silence(self.steps)
        self.status_var.set(f"Silence trimmed • {len(self.steps)} steps")

    def pad_press(self, event):
        if not self.recording or self.press is not None:
            return
        now = time.monotonic()
        frequency, strength = self._values(event)
        self.press = {"at": now, "start": frequency, "current": frequency,
                      "strength": strength, "samples": [(now, frequency, strength)]}
        live = self.press
        engine.interrupt_playback()
        engine.start_live_tone(
            self.live_name, lambda: (live["current"], live["strength"]))
        self.status_var.set(f"{frequency:.0f} Hz • {strength:.0%}")

    def pad_drag(self, event):
        if self.press is None:
            return
        self.pad_motion(event)
        frequency, strength = self._values(event)
        self.press["current"] = frequency
        self.press["strength"] = strength
        now = time.monotonic()
        if now - self.press["samples"][-1][0] >= 0.04:
            self.press["samples"].append((now, frequency, strength))
        self.status_var.set(f"{frequency:.0f} Hz • {strength:.0%}")

    def pad_release(self, event):
        if self.press is None:
            return
        released = time.monotonic()
        frequency, strength = self._values(event)
        self._stop_live()
        samples = self.press["samples"]
        samples.append((released, frequency, strength))
        self.steps = append_gesture(self.steps, samples, self.previous_release)
        self.previous_release = released
        self.press = None
        self.status_var.set(f"Recording… • {len(self.steps)} steps")

    def _stop_live(self):
        engine.stop_live_tone(self.live_name)
        engine.interrupt_playback()

    def preview(self):
        self.stop()
        try:
            definition = effect_definition("Preview", "Custom", "", self.steps)
        except ValueError as exc:
            messagebox.showwarning("Nothing to preview", str(exc), parent=self)
            return

        def play_preview():
            for step in definition["steps"]:
                if step["type"] == "wait":
                    time.sleep(step["duration"])
                else:
                    engine.play(engine.render_recipe_step(step))
        threading.Thread(target=play_preview, daemon=True).start()
        self.status_var.set("Previewing…")

    def save(self):
        self.stop()
        try:
            conflict = duplicate_effect_name(self.name_var.get())
            if conflict:
                raise ValueError(
                    f"An effect named '{conflict[1]}' already exists. Choose a unique name.")
            definition = effect_definition(
                self.name_var.get(), self.category_var.get(), self.description_var.get(), self.steps)
            catalog = load_user_effect_catalog()
            effects = catalog.get("effects")
            effect_id = unique_effect_id(self.name_var.get(), set(effects) |
                                         set(engine.EFFECT_DEFINITIONS))
            effects[effect_id] = definition
            write_user_effect_catalog(catalog)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            messagebox.showwarning("Could not save effect", str(exc), parent=self)
            return
        self.destroy()
        self.owner.reload_effect_catalog()

    def cancel(self):
        self._stop_live()
        self.destroy()


class EffectEditorDialog(tk.Toplevel):
    def __init__(self, manager, effect_id, definition):
        super().__init__(manager)
        self.manager = manager
        self.effect_id = effect_id
        self.title("Edit custom effect")
        self.transient(manager)
        self.resizable(False, False)
        self.name_var = tk.StringVar(value=definition.get("name", effect_id))
        self.category_var = tk.StringVar(value=definition.get("category", "Custom"))
        self.description_var = tk.StringVar(value=definition.get("description", ""))
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"Effect ID: {effect_id}", style="Muted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(frame, text="Name").grid(row=1, column=0, sticky="w")
        ttk.Label(frame, text="Category").grid(row=1, column=1, sticky="w", padx=(12, 0))
        self.name_entry = ttk.Entry(frame, textvariable=self.name_var, width=36)
        self.name_entry.grid(row=2, column=0, sticky="ew", pady=(3, 10))
        ttk.Entry(frame, textvariable=self.category_var, width=24).grid(
            row=2, column=1, sticky="ew", padx=(12, 0), pady=(3, 10))
        ttk.Label(frame, text="Description").grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Entry(frame, textvariable=self.description_var).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(3, 10))
        ttk.Label(frame, text="Sequence steps (JSON)").grid(
            row=5, column=0, columnspan=2, sticky="w")
        self.steps_text = tk.Text(frame, width=76, height=18, wrap="none", undo=True,
                                  background=manager.owner.palette["field"],
                                  foreground=manager.owner.palette["foreground"],
                                  insertbackground=manager.owner.palette["foreground"],
                                  selectbackground=manager.owner.palette["select"])
        self.steps_text.grid(row=6, column=0, columnspan=2, pady=(3, 12))
        self.steps_text.insert("1.0", json.dumps(definition.get("steps", []), indent=2))
        actions = ttk.Frame(frame)
        actions.grid(row=7, column=0, columnspan=2, sticky="e")
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="left")
        ttk.Button(actions, text="Save changes", command=self.save).pack(side="left", padx=(8, 0))
        manager.owner.bind_dialog_shortcuts(self, self.save, self.destroy)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        manager.owner.center_dialog(self)
        self.name_entry.focus_set()

    def save(self):
        try:
            name = self.name_var.get().strip()
            category = self.category_var.get().strip()
            if not name or not category:
                raise ValueError("Name and category are required.")
            conflict = duplicate_effect_name(name, self.effect_id)
            if conflict:
                raise ValueError(
                    f"An effect named '{conflict[1]}' already exists. Choose a unique name.")
            steps = validate_sequence_steps(json.loads(self.steps_text.get("1.0", "end")))
            catalog = load_user_effect_catalog()
            definition = catalog["effects"].get(self.effect_id)
            if not isinstance(definition, dict):
                raise ValueError("This custom effect no longer exists.")
            definition.update({
                "name": name, "category": category,
                "description": self.description_var.get().strip(),
                "generator": "sequence", "steps": steps,
            })
            write_user_effect_catalog(catalog)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            messagebox.showwarning("Could not update effect", str(exc), parent=self)
            return
        self.destroy()
        self.manager.catalog_changed()


class EffectManagerDialog(tk.Toplevel):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.title("Manage custom effects")
        self.transient(owner)
        self.geometry("760x460")
        self.minsize(680, 390)
        
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        
        # Top Labels
        ttk.Label(frame, text="Custom effects", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Edit recorded sequences or remove an override to restore its default.",
                  style="Muted.TLabel").pack(anchor="w", pady=(3, 10))
        
        # Bottom Actions Frame
        actions = ttk.Frame(frame)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        
        ttk.Button(actions, text="Edit selected…", command=self.edit_selected).pack(side="left")
        ttk.Button(actions, text="Preview", command=self.preview_selected).pack(
            side="left", padx=(8, 0))
        ttk.Button(actions, text="Delete…", command=self.delete_selected).pack(
            side="left", padx=(8, 0))
        ttk.Button(actions, text="Close", command=self.destroy).pack(side="right")
        
        # Middle Treeview
        self.tree = ttk.Treeview(frame, columns=("name", "category", "id"),
                                 show="headings", selectmode="browse")
        self.tree.heading("name", text="Name")
        self.tree.heading("category", text="Category")
        self.tree.heading("id", text="Effect ID")
        self.tree.column("name", width=220)
        self.tree.column("category", width=140)
        self.tree.column("id", width=280)
        self.tree.pack(side="top", fill="both", expand=True)        
        self.tree.bind("<Double-1>", lambda _event: self.edit_selected())
        
        # Window & Protocol Configurations
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        owner.bind_dialog_shortcuts(self, self.edit_selected, self.destroy)
        self.grab_set()
        owner.center_dialog(self)
        self.refresh()


    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        try:
            effects = load_user_effect_catalog()["effects"]
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            messagebox.showwarning("Could not load custom effects", str(exc), parent=self)
            return
        for effect_id, definition in sorted(
                effects.items(), key=lambda item: str(item[1].get("name", item[0])).casefold()):
            self.tree.insert("", "end", iid=effect_id, values=(
                definition.get("name", effect_id), definition.get("category", ""), effect_id))

    def selected(self):
        selection = self.tree.selection()
        return selection[0] if selection else None

    def edit_selected(self):
        effect_id = self.selected()
        if not effect_id:
            return
        definition = load_user_effect_catalog()["effects"].get(effect_id, {})
        if definition.get("generator") != "sequence":
            messagebox.showwarning("Cannot edit effect",
                                   "Only recorded sequence effects can be edited here.", parent=self)
            return
        EffectEditorDialog(self, effect_id, definition)

    def preview_selected(self):
        effect_id = self.selected()
        effect = engine.get_effect_registry().get(effect_id) if effect_id else None
        if effect:
            engine.interrupt_playback()
            engine.queue_haptic(effect)

    def delete_selected(self):
        effect_id = self.selected()
        if not effect_id:
            return
        catalog = load_user_effect_catalog()
        definition = catalog["effects"].get(effect_id, {})
        restoring = is_builtin_effect_id(effect_id)
        detail = ("The built-in effect will be restored."
                  if restoring else "Mappings that use this effect will no longer play it.")
        if not messagebox.askyesno(
                "Delete custom effect",
                f"Delete '{definition.get('name', effect_id)}'?\n\n{detail}", parent=self):
            return
        catalog["effects"].pop(effect_id, None)
        write_user_effect_catalog(catalog)
        self.catalog_changed()

    def catalog_changed(self):
        self.owner.reload_effect_catalog(notify=False)
        self.refresh()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        engine.load_config()
        self.runtime = Runtime()
        self.title("Elite Haptics")
        if os.path.exists(APP_ICON_PATH):
            try:
                self.iconbitmap(APP_ICON_PATH)
            except tk.TclError:
                pass
        self.overrideredirect(True)
        self._window_maximized = False
        self._restore_geometry = None
        self._drag_origin = None
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

        self.status_text = tk.StringVar(value="Monitor disabled")
        self._monitoring_state = "disabled"
        self._runtime_ready = False
        self._journal_activity_seen = False
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
        self.bind("<Map>", self.restore_custom_chrome, add="+")
        self.after(150, self.start_runtime)
        self.after(200, self.poll_monitor_events)
        self.after(600, self.show_effect_migration_notice)
        self.after(1000, self.refresh_monitoring_status)
        self.after(10, self.enable_taskbar_window)

    def _build(self):
        header = ttk.Frame(self, style="Titlebar.TFrame", padding=(12, 0))
        header.pack(fill="x", padx=18, pady=(14, 0))
        title_label = ttk.Label(
            header, text="◈  ELITE  /  HAPTICS", style="Title.TLabel"
        )
        title_label.pack(side="left", pady=10)
        for widget in (header, title_label):
            widget.bind("<ButtonPress-1>", self.start_window_drag)
            widget.bind("<B1-Motion>", self.drag_window)
            widget.bind("<Double-Button-1>", lambda _: self.toggle_maximize())

        ttk.Button(header, text=WINDOW_GLYPHS["close"], style="Chrome.TButton", width=4,
                   command=self.close).pack(side="right", padx=(3, 0), pady=5)
        self.maximize_button = ttk.Button(
            header, text=WINDOW_GLYPHS["maximize"], style="Chrome.TButton", width=4,
            command=self.toggle_maximize)
        self.maximize_button.pack(side="right", padx=3, pady=5)
        ttk.Button(header, text=WINDOW_GLYPHS["minimize"], style="Chrome.TButton", width=4,
                   command=self.minimize_window).pack(side="right", padx=3, pady=5)
        self.status_label = ttk.Label(
            header, textvariable=self.status_text, style="HeaderStatus.TLabel",
            width=26, anchor="w"
        )
        self.status_label.pack(side="right", padx=(4, 10), pady=10)
        self.status_dot = ttk.Label(header, text="●", style="StatusDisabled.TLabel")
        self.status_dot.pack(side="right", padx=(10, 0), pady=(9, 11))
        for widget in (self.status_dot, self.status_label):
            widget.bind("<ButtonPress-1>", self.start_window_drag)
            widget.bind("<B1-Motion>", self.drag_window)
            widget.bind("<Double-Button-1>", lambda _: self.toggle_maximize())

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=18, pady=(12, 14))
        bindings_tab = ttk.Frame(self.notebook, padding=14)
        self.pads_tab = ttk.Frame(self.notebook, padding=14)
        monitoring_tab = ttk.Frame(self.notebook, padding=14)
        self.settings_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(bindings_tab, text="Effects")
        self.notebook.add(self.pads_tab, text="Test Pad")
        self.notebook.add(monitoring_tab, text="Monitoring")
        self.notebook.add(self.settings_tab, text="Settings")
        self._build_bindings(bindings_tab)
        self._build_pads(self.pads_tab)
        self._build_monitoring(monitoring_tab)
        self._build_settings(self.settings_tab)
        self.bind_all("<KeyPress>", self.on_app_keypress, add="+")
        self.size_grip = ttk.Sizegrip(self)
        self.size_grip.place(relx=1.0, rely=1.0, anchor="se")

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

        tree_frame = ttk.Frame(parent)
        tree_frame.grid(row=5, column=0, columnspan=4, sticky="nsew")
        self.binding_tree = ttk.Treeview(
            tree_frame, columns=("category", "trigger", "effect", "intensity", "enabled", "source"),
            show="headings", height=15, selectmode="extended")
        headings = {
            "category": "Category", "trigger": "Telemetry trigger", "effect": "Effect",
            "intensity": "Strength", "enabled": "Enabled", "source": "Source",
        }
        for column, label in headings.items():
            self.binding_tree.heading(column, text=label,
                                      command=lambda c=column: self.sort_bindings(c))
        self.binding_tree.column("category", width=110)
        self.binding_tree.column("trigger", width=235)
        self.binding_tree.column("effect", width=245)
        self.binding_tree.column("intensity", width=90, anchor="center")
        self.binding_tree.column("enabled", width=75, anchor="center", stretch=False)
        self.binding_tree.column("source", width=85, anchor="center")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.binding_tree.yview)
        self.binding_tree.configure(yscrollcommand=tree_scroll.set)
        self.binding_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.binding_tree.bind("<Double-1>", lambda _: self.edit_selected())
        self.binding_tree.bind("<Button-3>", self.show_binding_context_menu)
        self.binding_tree.bind("<Shift-F10>", self.show_binding_context_menu)
        self.binding_tree.bind("<<TreeviewSelect>>", lambda _: self.show_selected_notice())
        self.binding_tree.tag_configure("special", foreground="#C85B67")
        self.binding_tree.tag_configure("disabled", foreground="#7C8791")

        self.binding_context_menu = tk.Menu(self, tearoff=False)
        self.binding_context_menu.add_command(label="Test", command=self.test_selected)
        self.binding_context_menu.add_command(label="Edit…", command=self.edit_selected)
        self.binding_context_menu.add_separator()
        self.binding_context_menu.add_command(
            label="Disable mapping", command=self.toggle_selected_enabled)
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
        ttk.Button(parent, text="Recent changes",
                   command=lambda: self.show_effect_migration_notice(force=True)).grid(
                       row=0, column=1, sticky="e", pady=(0, 16))
        ttk.Label(parent, text="Theme").grid(row=1, column=0, sticky="w", pady=(0, 16))
        self.theme_combo = ttk.Combobox(
            parent, textvariable=self.theme_var, values=list(THEME_OPTIONS.values()),
            state="readonly", width=20)
        self.theme_combo.grid(row=1, column=1, sticky="e", pady=(0, 16))
        self.theme_combo.bind("<<ComboboxSelected>>", lambda _: self.apply_theme())
        ttk.Label(parent, text="Master strength").grid(row=2, column=0, sticky="w")
        ttk.Scale(parent, from_=0.0, to=1.0, variable=self.master_var,
                  command=self.change_master).grid(row=3, column=0, sticky="ew", pady=(4, 16))
        self.master_label = ttk.Label(parent, text=f"{engine.MASTER_VOLUME:.0%}")
        self.master_label.grid(row=3, column=1, padx=10)
        ttk.Label(parent, text="Audio output device").grid(row=4, column=0, sticky="w")
        try:
            devices = [dev["name"] for dev in engine.sd.query_devices()
                       if dev["max_output_channels"] > 0]
        except Exception:
            devices = [engine.OUTPUT_DEVICE_NAME]
        ttk.Combobox(parent, textvariable=self.device_var, values=devices,
                     state="readonly").grid(row=5, column=0, columnspan=2,
                                            sticky="ew", pady=(4, 14))
        ttk.Label(parent, text="Elite Dangerous journal folder").grid(row=6, column=0, sticky="w")
        journal_row = ttk.Frame(parent)
        journal_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 14))
        ttk.Entry(journal_row, textvariable=self.journal_var).pack(side="left", fill="x", expand=True)
        ttk.Button(journal_row, text="Browse…", command=self.browse_journal).pack(
            side="left", padx=(8, 0))
        settings_actions = ttk.Frame(parent)
        settings_actions.grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Button(settings_actions, text="Apply and reconnect", command=self.apply_settings).pack(
            side="left")
        ttk.Button(settings_actions, text="Reset all defaults…", command=self.reset_all_defaults).pack(
            side="left", padx=(8, 0))
        ttk.Label(parent, text="Applying briefly restarts audio and journal monitoring.",
                  style="Muted.TLabel").grid(row=9, column=0, columnspan=2,
                                             sticky="w", pady=(8, 0))
        ttk.Separator(parent).grid(row=10, column=0, columnspan=2, sticky="ew", pady=(20, 14))
        ttk.Label(parent, text="Effect catalog").grid(row=11, column=0, sticky="w")
        effect_actions = ttk.Frame(parent)
        effect_actions.grid(row=12, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(effect_actions, text="Open effects.json",
                   command=self.open_effects_file).pack(side="left")
        ttk.Button(effect_actions, text="Reload effects",
                   command=self.reload_effect_catalog).pack(side="left", padx=(8, 0))
        ttk.Button(effect_actions, text="Record effect…",
                   command=self.open_effect_recorder).pack(side="left", padx=(8, 0))
        ttk.Button(effect_actions, text="Manage effects…",
                   command=self.open_effect_manager).pack(side="left", padx=(8, 0))
        ttk.Button(effect_actions, text="Effect guide",
                   command=self.open_effects_guide).pack(side="left", padx=(8, 0))
        ttk.Label(parent, text=engine.EFFECTS_CONFIG_PATH, style="Muted.TLabel",
                  wraplength=720).grid(row=13, column=0, columnspan=2,
                                       sticky="w", pady=(8, 0))
        ttk.Label(parent, text="Trigger catalog").grid(
            row=14, column=0, sticky="w", pady=(18, 0))
        trigger_actions = ttk.Frame(parent)
        trigger_actions.grid(row=15, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(trigger_actions, text="Open triggers.json",
                   command=self.open_triggers_file).pack(side="left")
        ttk.Button(trigger_actions, text="Reload triggers",
                   command=self.reload_trigger_catalog).pack(side="left", padx=(8, 0))
        ttk.Button(trigger_actions, text="Trigger guide",
                   command=self.open_triggers_guide).pack(side="left", padx=(8, 0))
        ttk.Label(parent, text=engine.TRIGGERS_CONFIG_PATH, style="Muted.TLabel",
                  wraplength=720).grid(row=16, column=0, columnspan=2,
                                       sticky="w", pady=(8, 0))
        parent.columnconfigure(0, weight=1)

    def open_effects_file(self):
        try:
            os.startfile(engine.EFFECTS_CONFIG_PATH)
        except (AttributeError, OSError) as exc:
            messagebox.showwarning("Could not open effects.json", str(exc))

    def open_effect_recorder(self):
        EffectRecorderDialog(self)

    def open_effect_manager(self):
        EffectManagerDialog(self)

    def open_default_effects_file(self):
        try:
            os.startfile(engine.EFFECTS_DEFAULT_PATH)
        except (AttributeError, OSError) as exc:
            messagebox.showwarning("Could not open built-in effects", str(exc))

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

    def reload_effect_catalog(self, notify=True):
        try:
            with open(engine.EFFECTS_CONFIG_PATH, "r", encoding="utf-8") as file:
                catalog = json.load(file)
            definitions = catalog.get("effects")
            if not isinstance(definitions, dict):
                raise ValueError("The override catalog must contain an 'effects' object.")
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
        if notify:
            messagebox.showinfo(
                "Effects reloaded",
                f"Loaded {len(engine.EFFECT_DEFINITIONS)} merged effects. User overrides: {len(definitions)}.")

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
            if source == "Journal":
                self._journal_activity_seen = True
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
                "trough": "#182839", "special": "#E47A86", "online": "#71C49A",
            }
        elif theme_id == "elite_amber":
            palette = {
                "background": "#000000", "surface": "#1A0D00", "field": "#0D0700",
                "foreground": "#FFE3C2", "muted": "#C58A55", "border": "#5A2B00",
                "select": "#3A1900", "accent": "#FF7100", "accent_hover": "#FF9A3D",
                "hover": "#3D1E00", "hover_foreground": "#FFFFFF",
                "trough": "#211000", "special": "#E47A86", "online": "#71C49A",
            }
        else:
            palette = {
                "background": "#F3F5F7", "surface": "#FFFFFF", "field": "#FFFFFF",
                "foreground": "#20272C", "muted": "#647583", "border": "#C5CED6",
                "select": "#DCEAF7", "accent": "#3478B8", "accent_hover": "#24679F",
                "hover": "#E1E7ED", "hover_foreground": "#20272C",
                "trough": "#D4DCE3", "special": "#C85B67", "online": "#39865F",
            }
        self.style.theme_use("clam")
        self.palette = dict(palette)
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
        self.style.configure("StatusOnline.TLabel", background=palette["surface"],
                             foreground=palette["online"], font=("Segoe UI", 9))
        self.style.configure("StatusOffline.TLabel", background=palette["surface"],
                             foreground=palette["special"], font=("Segoe UI", 9))
        self.style.configure("StatusDisabled.TLabel", background=palette["surface"],
                             foreground=palette["muted"], font=("Segoe UI", 9))
        self.style.configure("ChangelogNote.TLabel", background=palette["background"],
                             foreground=palette["muted"], font=("Segoe UI", 8))

        if hasattr(self, "status_dot"):
            status_styles = {
                "monitoring": "StatusOnline.TLabel",
                "awaiting": "StatusOffline.TLabel",
                "disabled": "StatusDisabled.TLabel",
            }
            self.status_dot.configure(
                style=status_styles.get(self._monitoring_state, "StatusDisabled.TLabel"))
        self.style.configure("Chrome.TButton", background=palette["surface"],
                             foreground=palette["foreground"], padding=(10, 7),
                             borderwidth=0, relief="flat",
                             font=("Segoe Fluent Icons", 8))
        self.style.map("Chrome.TButton",
                       background=[("pressed", palette["select"]),
                                   ("active", palette["hover"])],
                       foreground=[("pressed", palette["accent"]),
                                   ("active", palette["hover_foreground"])])
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
        self.option_add("*TCombobox*Listbox.selectForeground", palette["accent"])
        if hasattr(self, "binding_tree"):
            self.binding_tree.tag_configure("special", foreground=palette["special"])
            self.binding_tree.tag_configure("disabled", foreground=palette["muted"])
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
            self._runtime_ready = False
            self.set_monitoring_status("disabled")
            self.runtime.stop()
            self.runtime = Runtime()
            self.runtime.start()
            self._runtime_ready = True
            self._journal_activity_seen = False
            self.set_monitoring_status("awaiting")
        except Exception as exc:
            self._runtime_ready = False
            self.set_monitoring_status("disabled")
            messagebox.showwarning("Could not reconnect", str(exc))

    def reset_all_defaults(self):
        if not messagebox.askyesno(
                "Reset all defaults",
                "Reset settings, effect mappings, added bindings, and Test Pad modes?"):
            return
        engine.load_default_config()
        defaults = engine.DEFAULT_CONFIG
        settings = defaults.get("settings", {})
        engine.MASTER_VOLUME = float(settings.get("master_volume", 0.5))
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
            self._runtime_ready = True
            self._journal_activity_seen = False
            self.set_monitoring_status("awaiting")
        except Exception as exc:
            self._runtime_ready = False
            self.set_monitoring_status("disabled")
            messagebox.showwarning("Could not start monitoring", str(exc))

    @staticmethod
    def elite_dangerous_running():
        """Return whether the live Elite Dangerous client is running on Windows."""
        if sys.platform != "win32":
            return True
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq EliteDangerous64.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False)
            return "EliteDangerous64.exe" in result.stdout
        except (OSError, subprocess.SubprocessError):
            return False

    def refresh_monitoring_status(self):
        """Show green only after live telemetry arrives while the game is running."""
        if self._runtime_ready:
            active = self._journal_activity_seen and self.elite_dangerous_running()
            self.set_monitoring_status("monitoring" if active else "awaiting")
        else:
            self.set_monitoring_status("disabled")
        self.after(2000, self.refresh_monitoring_status)

    def set_monitoring_status(self, state):
        """Update the header text and its independently colored status dot."""
        states = {
            "monitoring": ("Monitoring Elite Dangerous", "StatusOnline.TLabel"),
            "awaiting": ("Awaiting connection...", "StatusOffline.TLabel"),
            "disabled": ("Monitor disabled", "StatusDisabled.TLabel"),
        }
        self._monitoring_state = state if state in states else "disabled"
        text, style = states[self._monitoring_state]
        self.status_text.set(text)
        if hasattr(self, "status_dot"):
            self.status_dot.configure(style=style)

    def show_effect_migration_notice(self, force=False):
        """Show the one-time effect changelog and every compensated user value."""
        notice = engine.PENDING_EFFECT_MIGRATION_NOTICE
        if not notice:
            if force:
                messagebox.showinfo("Recent effect changes", "No effect migration has been applied yet.")
            return
        if notice.get("acknowledged") and not force:
            return

        version = notice.get("catalog_version", "the latest version")
        backup_path = notice.get("backup_path")

        dialog = tk.Toplevel(self)
        dialog.title(f"What's new in Elite Haptics {version}")
        if os.path.exists(APP_ICON_PATH):
            try:
                dialog.iconbitmap(APP_ICON_PATH)
            except tk.TclError:
                pass
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, width=84, height=24,
                       wrap="word", relief="flat", borderwidth=0, padx=8, pady=8)
        palette = getattr(self, "palette", {})
        text.configure(background=palette.get("field", "#FFFFFF"),
                       foreground=palette.get("foreground", "#20272C"),
                       insertbackground=palette.get("foreground", "#20272C"),
                       selectbackground=palette.get("select", "#DCEAF7"),
                       font=("Segoe UI", 10))
        text.tag_configure("h1", font=("Segoe UI Semibold", 15),
                           foreground=palette.get("accent", "#3478B8"), spacing3=8)
        text.tag_configure("intro", font=("Segoe UI", 10), spacing3=14)
        text.tag_configure("h2", font=("Segoe UI Semibold", 11),
                           foreground=palette.get("accent", "#3478B8"), spacing1=8, spacing3=3)
        text.tag_configure("h3", font=("Segoe UI Semibold", 10),
                           foreground=palette.get("foreground", "#20272C"),
                           spacing1=6, spacing3=3)
        text.tag_configure("indented_h3", font=("Segoe UI Semibold", 10),
                           foreground=palette.get("foreground", "#20272C"),
                           lmargin1=28, lmargin2=28, spacing1=6, spacing3=3)
        text.tag_configure("body", font=("Segoe UI", 10), spacing3=4)
        text.tag_configure("bullet", font=("Segoe UI", 10), lmargin1=14, lmargin2=28, spacing3=4)
        text.tag_configure("nested_bullet", font=("Segoe UI", 10),
                           lmargin1=34, lmargin2=50, spacing3=4)
        text.tag_configure("indented_text", font=("Segoe UI", 10),
                           lmargin1=28, lmargin2=28, spacing3=4)
        text.tag_configure("note", font=("Segoe UI", 9),
                           foreground=palette.get("muted", "#647583"), spacing1=12)
        text.tag_configure("strong", font=("Segoe UI Semibold", 10))

        def insert_inline_markdown(value, tag):
            parts = value.split("**")
            for index, part in enumerate(parts):
                text.insert("end", part, (tag, "strong") if index % 2 else tag)

        def insert_effect_updates():
            for effect in notice.get("effects", []):
                text.insert(
                    "end", f"{effect.get('effect_name') or effect.get('effect') or 'Effect'}\n",
                    "h2")
                text.insert(
                    "end", effect.get("reason", "Built-in effect updated").rstrip(".") + "\n",
                    "body")
                if effect.get("skipped_user_override"):
                    text.insert(
                        "end",
                        "• Your custom waveform was preserved, so its strengths were not changed\n",
                        "bullet")
                elif effect.get("adjustments"):
                    for adjustment in effect["adjustments"]:
                        insert_inline_markdown(
                            "• Your customized strength was adjusted from "
                            f"**{float(adjustment['previous']):.2f}** to "
                            f"**{float(adjustment['current']):.2f}** as a result",
                            "bullet")
                        text.insert("end", "\n")
                else:
                    text.insert("end", "• No customized mapping strengths required adjustment\n",
                                "bullet")

        def insert_backup_details():
            if backup_path:
                text.insert("end", "A backup was created here before applying these changes:\n", "body")
                text.insert("end", f"{backup_path}\n", "bullet")
            else:
                text.insert(
                    "end", "No previous haptics_config.json existed, so no backup was required\n",
                    "body")

        try:
            with open(CHANGELOG_CONFIG_PATH, "r", encoding="utf-8") as file:
                changelog_blocks = json.load(file).get("blocks", [])
        except (OSError, json.JSONDecodeError):
            changelog_blocks = [
                {"type": "heading1", "text": "Effect updates in Elite Haptics {{version}}"},
                {"type": "placeholder", "name": "effect_updates"},
                {"type": "heading2", "text": "Configuration backup"},
                {"type": "placeholder", "name": "backup_details"},
            ]

        block_tags = {
            "heading1": "h1", "heading2": "h2", "heading3": "h3",
            "indented_heading3": "indented_h3", "paragraph": "intro",
            "bullet": "bullet", "nested_bullet": "nested_bullet",
            "indented_text": "indented_text", "note": "note",
        }
        inserted_effect_updates = False
        inserted_backup_details = False
        for block in changelog_blocks:
            block_type = block.get("type")
            if block_type == "placeholder":
                if block.get("name") == "effect_updates":
                    insert_effect_updates()
                    inserted_effect_updates = True
                elif block.get("name") == "backup_details":
                    insert_backup_details()
                    inserted_backup_details = True
                continue
            value = str(block.get("text", "")).replace("{{version}}", str(version))
            if block_type == "bullet":
                value = f"• {value}"
            elif block_type == "nested_bullet":
                value = f"• {value}"
            insert_inline_markdown(value, block_tags.get(block_type, "body"))
            text.insert("end", "\n")

        if not inserted_effect_updates:
            text.insert("end", "Customized effect strength adjustments\n", "h2")
            text.insert(
                "end",
                "The following customized effect strength levels have been adjusted accordingly\n",
                "intro")
            insert_effect_updates()
        if not inserted_backup_details:
            text.insert("end", "Configuration backup\n", "h2")
            insert_backup_details()
        text.configure(state="disabled")
        text.pack(fill="both", expand=True, pady=(10, 12))

        ttk.Label(
            frame,
            text=("You can view this changelog at any time by clicking Recent changes "
                  "in the top-right corner of the Settings tab"),
            style="ChangelogNote.TLabel", wraplength=650).pack(anchor="w", pady=(0, 10))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")

        def open_backup():
            if backup_path and os.path.exists(backup_path):
                os.startfile(os.path.dirname(backup_path))
            else:
                messagebox.showinfo("Configuration backup", "The recorded backup is not available.",
                                    parent=dialog)

        def acknowledge():
            notice["acknowledged"] = True
            engine.save_config()
            dialog.destroy()

        if backup_path:
            ttk.Button(buttons, text="Open backup folder", command=open_backup).pack(side="left")
        ttk.Button(buttons, text="OK", command=acknowledge).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", acknowledge)
        self.bind_dialog_shortcuts(dialog, acknowledge, acknowledge)
        self.center_dialog(dialog)

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
                      "Yes" if binding.get("enabled", True) else "No",
                      binding.get("source", "Custom"))
            if category_filter != "All categories" and category != category_filter:
                continue
            if needle and needle not in " ".join(values).lower():
                continue
            decorated.append((iid, binding, values, special))

        column_index = {"category": 0, "trigger": 1, "effect": 2,
                        "intensity": 3, "enabled": 4, "source": 5}[self.sort_column]
        if self.sort_column == "category":
            decorated.sort(
                key=lambda row: (str(row[2][0]).lower(), str(row[2][1]).lower()),
                reverse=self.sort_reverse)
        else:
            decorated.sort(
                key=lambda row: str(row[2][column_index]).lower(),
                reverse=self.sort_reverse)
        for iid, binding, values, special in decorated:
            tags = []
            if special is True:
                tags.append("special")
            if not binding.get("enabled", True):
                tags.append("disabled")
            self.binding_tree.insert("", "end", iid=iid, values=values,
                                     tags=tuple(tags))

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
        toggleable = bool(binding and binding.get("action") != "continuous_stop")
        self.binding_context_menu.entryconfigure("Test", state="normal" if testable else "disabled")
        self.binding_context_menu.entryconfigure("Edit…", state="normal" if editable else "disabled")
        self.binding_context_menu.entryconfigure(
            3, label=("Disable mapping" if binding and binding.get("enabled", True)
                      else "Enable mapping"),
            state="normal" if toggleable else "disabled")
        self.binding_context_menu.entryconfigure("Remove", state="normal" if kind == "custom" else "disabled")
        self.binding_context_menu.entryconfigure(
            "Reset default", state="normal" if kind == "builtin" else "disabled")
        try:
            self.binding_context_menu.tk_popup(x, y)
        finally:
            self.binding_context_menu.grab_release()
        return "break"

    def toggle_selected_enabled(self):
        """Toggle selected mappings while preserving automatic stop controls."""
        selected = self.binding_tree.selection()
        if not selected:
            return
        _, _, first = self._selected_binding()
        target = not first.get("enabled", True) if first else True
        for iid in selected:
            kind, raw_index = iid.split(":", 1)
            index = int(raw_index)
            if kind == "builtin":
                bindings = engine.get_builtin_bindings()
                if index >= len(bindings) or bindings[index].get("action") == "continuous_stop":
                    continue
                trigger = bindings[index]["trigger"]
                override = dict(engine.BUILTIN_OVERRIDES.get(trigger, {}))
                override["enabled"] = target
                engine.BUILTIN_OVERRIDES[trigger] = override
            elif index < len(engine.CUSTOM_BINDINGS):
                engine.CUSTOM_BINDINGS[index]["enabled"] = target
        engine.save_config()
        self.refresh_bindings()

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

        def activate():
            try:
                dialog.lift()
                dialog.focus_force()
            except tk.TclError:
                pass

        dialog.after_idle(activate)

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

    def enable_taskbar_window(self):
        """Keep a borderless Tk window represented normally on the Windows taskbar."""
        if sys.platform != "win32":
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            style = (style & ~0x00000080) | 0x00040000
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
        except (AttributeError, OSError, tk.TclError):
            pass

    def start_window_drag(self, event):
        self._drag_origin = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def drag_window(self, event):
        if not self._drag_origin:
            return
        if self._window_maximized:
            pointer_ratio = event.x_root / max(1, self.winfo_screenwidth())
            self.toggle_maximize()
            self.update_idletasks()
            new_x = int(event.x_root - self.winfo_width() * pointer_ratio)
            new_y = max(0, event.y_root - 18)
            self.geometry(f"+{new_x}+{new_y}")
            self._drag_origin = (event.x_root - new_x, event.y_root - new_y)
        x = event.x_root - self._drag_origin[0]
        y = event.y_root - self._drag_origin[1]
        self.geometry(f"+{x}+{y}")

    def minimize_window(self):
        # A borderless Tk window must temporarily regain native chrome before
        # iconifying, or Windows removes it from the taskbar, leaving it orphaned
        self.overrideredirect(False)
        self.iconify()

    def restore_custom_chrome(self, event=None):
        """Reapply the custom title bar after restoring from the taskbar."""
        if event is not None and event.widget is not self:
            return
        if self.state() == "normal" and not self.overrideredirect():
            self.after_idle(lambda: self.overrideredirect(True))

    def toggle_maximize(self):
        if self._window_maximized:
            if self._restore_geometry:
                self.geometry(self._restore_geometry)
            self._window_maximized = False
            self.maximize_button.configure(text=WINDOW_GLYPHS["maximize"])
            self.size_grip.place(relx=1.0, rely=1.0, anchor="se")
            return

        self._restore_geometry = self.geometry()
        if sys.platform == "win32":
            class Rect(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            area = Rect()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(area), 0):
                self.geometry(
                    f"{area.right - area.left}x{area.bottom - area.top}+{area.left}+{area.top}")
            else:
                self.geometry(
                    f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        else:
            self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        self._window_maximized = True
        self.maximize_button.configure(text=WINDOW_GLYPHS["restore"])
        self.size_grip.place_forget()

    def on_window_configure(self, event):
        if event.widget is not self or self.state() != "normal" or self._window_maximized:
            return
        if self._geometry_save_job is not None:
            self.after_cancel(self._geometry_save_job)
        self._geometry_save_job = self.after(500, self.persist_window_geometry)

    def persist_window_geometry(self):
        self._geometry_save_job = None
        if self.state() == "normal" and not self._window_maximized:
            engine.WINDOW_GEOMETRY = self.geometry()
            engine.save_config()

    def close(self):
        engine.remove_monitor_listener(self.monitor_queue.put)
        if self._geometry_save_job is not None:
            self.after_cancel(self._geometry_save_job)
            self._geometry_save_job = None
        if self.state() == "normal" and not self._window_maximized:
            engine.WINDOW_GEOMETRY = self.geometry()
        engine.save_config()
        self.runtime.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
