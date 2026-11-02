"""Core telemetry, waveform, and audio engine for Elite Haptics."""

import json
import os
import time
import glob
import threading
import numpy as np
import queue
import random
import sounddevice as sd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Fixed engine format. User settings are loaded from the JSON configuration.
SAMPLE_RATE = 44100

# Emergency fallbacks are used only if default_config.json is unavailable.
FALLBACK_SETTINGS = {
    "master_volume": 0.25,
    "output_device": "Voicemeeter Input",
    "theme": "dark_blue",
    "window_geometry": "940x780",
}

# Persisted settings and collections, populated by load_config().
MASTER_VOLUME = FALLBACK_SETTINGS["master_volume"]
OUTPUT_DEVICE_NAME = FALLBACK_SETTINGS["output_device"]
COLOR_THEME = FALLBACK_SETTINGS["theme"]
WINDOW_GEOMETRY = FALLBACK_SETTINGS["window_geometry"]
CUSTOM_BINDINGS = []
BUILTIN_OVERRIDES = {}
TEST_PAD_MODES = {"Default": {}}

# Transient telemetry and UI-observer state; intentionally never persisted.
CURRENT_STATUS_FLAGS = 0
CURRENT_HULL_HEALTH = 1.0
MONITOR_LISTENERS = []


def add_monitor_listener(listener):
    if listener not in MONITOR_LISTENERS:
        MONITOR_LISTENERS.append(listener)


def remove_monitor_listener(listener):
    if listener in MONITOR_LISTENERS:
        MONITOR_LISTENERS.remove(listener)


def emit_monitor_event(source, name, details=""):
    """Publish a lightweight session event without coupling the engine to the UI."""
    event = {"source": source, "name": name, "details": details}
    for listener in list(MONITOR_LISTENERS):
        try:
            listener(event)
        except Exception:
            pass

# Trigger catalogs are populated from triggers.json.
JOURNAL_TRIGGER_IDS = {}
TRIGGER_METADATA = {}
EVENT_MAP = {}
FLAG_WATCHERS = []
STATUS_TRIGGER_NAMES = []

# Path to script configuration file
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "haptics_config.json")
DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "default_config.json")
EFFECTS_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "effects.json")
TRIGGERS_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "triggers.json")
DEFAULT_CONFIG = {}
EFFECT_DEFINITIONS = {}
EXTERNAL_EFFECTS = {}
EFFECTS_CONFIG_MTIME = None
TRIGGERS_CONFIG_MTIME = None

# Path to Elite Dangerous journal files
JOURNAL_DIR = os.path.expandvars(
    r"%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous"
)


def load_trigger_definitions():
    """Load journal mappings, display metadata, and Status flag transitions."""
    global JOURNAL_TRIGGER_IDS, TRIGGER_METADATA
    global EVENT_MAP, FLAG_WATCHERS, STATUS_TRIGGER_NAMES, TRIGGERS_CONFIG_MTIME
    try:
        modified = os.path.getmtime(TRIGGERS_CONFIG_PATH)
        if TRIGGERS_CONFIG_MTIME == modified:
            return
        with open(TRIGGERS_CONFIG_PATH, "r", encoding="utf-8") as file:
            catalog = json.load(file)

        journal_events = catalog.get("journal_events", {})
        trigger_definitions = catalog.get("triggers", {})
        status_flags = catalog.get("status_flags", [])
        if not isinstance(journal_events, dict) or not isinstance(trigger_definitions, dict):
            raise ValueError("Trigger catalog objects are malformed.")

        metadata = {}
        for trigger_id, definition in trigger_definitions.items():
            annotation = definition.get("annotation")
            marker = True if annotation == "special" else (annotation or False)
            metadata[trigger_id] = (
                definition.get("category", "Other"),
                definition.get("name", trigger_id.replace("_", " ").title()),
                marker,
                definition.get("note", ""),
            )

        watchers = [
            (int(item["bit"]), item["on"], item["off"])
            for item in status_flags
        ]
        JOURNAL_TRIGGER_IDS = dict(journal_events)
        TRIGGER_METADATA = metadata
        EVENT_MAP = dict(journal_events)
        FLAG_WATCHERS = watchers
        STATUS_TRIGGER_NAMES = [
            name for _, on_name, off_name in watchers for name in (on_name, off_name)
        ]
        TRIGGERS_CONFIG_MTIME = modified
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"[WARNING] Could not load trigger catalog: {error}")


load_trigger_definitions()

# ============================================================
# UTILITY - List available audio devices
# Uncomment and run this first to find your Voicemeeter device name
# ============================================================

def load_config():
    """Load shipped defaults, then overlay the user's saved preferences."""
    global MASTER_VOLUME, CUSTOM_BINDINGS, BUILTIN_OVERRIDES
    global TEST_PAD_MODES, OUTPUT_DEVICE_NAME, JOURNAL_DIR, COLOR_THEME, WINDOW_GEOMETRY
    load_trigger_definitions()
    load_default_config()
    settings = DEFAULT_CONFIG.get("settings", {})
    MASTER_VOLUME = settings.get("master_volume", FALLBACK_SETTINGS["master_volume"])
    OUTPUT_DEVICE_NAME = settings.get("output_device", FALLBACK_SETTINGS["output_device"])
    JOURNAL_DIR = os.path.expandvars(settings.get("journal_directory", JOURNAL_DIR))
    COLOR_THEME = settings.get("theme", FALLBACK_SETTINGS["theme"])
    WINDOW_GEOMETRY = settings.get("window_geometry", FALLBACK_SETTINGS["window_geometry"])
    CUSTOM_BINDINGS = list(DEFAULT_CONFIG.get("custom_bindings", []))
    BUILTIN_OVERRIDES = {}
    TEST_PAD_MODES = dict(DEFAULT_CONFIG.get("test_pad_modes", {"Default": {}}))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            MASTER_VOLUME = data.get("master_volume", MASTER_VOLUME)
            OUTPUT_DEVICE_NAME = data.get("output_device", OUTPUT_DEVICE_NAME)
            JOURNAL_DIR = os.path.expandvars(data.get("journal_directory", JOURNAL_DIR))
            COLOR_THEME = data.get("theme", COLOR_THEME)
            WINDOW_GEOMETRY = data.get("window_geometry", WINDOW_GEOMETRY)
            CUSTOM_BINDINGS = data.get("custom_bindings", CUSTOM_BINDINGS)
            BUILTIN_OVERRIDES = data.get("builtin_overrides", BUILTIN_OVERRIDES)
            if "test_pad_modes" in data:
                TEST_PAD_MODES = data["test_pad_modes"]
        except (json.JSONDecodeError, OSError):
            pass  # corrupt or unreadable - just use the default

def load_default_config():
    """Load the shipped reset values and default trigger mappings."""
    global DEFAULT_CONFIG
    try:
        with open(DEFAULT_CONFIG_PATH, "r") as file:
            DEFAULT_CONFIG = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[WARNING] Could not load default configuration: {error}")
        DEFAULT_CONFIG = {"settings": {}, "bindings": [], "test_pad_modes": {"Default": {}}}

def save_config():
    """Persist current settings so they survive a restart."""
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump({
                "schema_version": 1,
                "master_volume": MASTER_VOLUME,
                "output_device": OUTPUT_DEVICE_NAME,
                "journal_directory": JOURNAL_DIR,
                "theme": COLOR_THEME,
                "window_geometry": WINDOW_GEOMETRY,
                "custom_bindings": CUSTOM_BINDINGS,
                "builtin_overrides": BUILTIN_OVERRIDES,
                "test_pad_modes": TEST_PAD_MODES,
            }, f, indent=2)
    except OSError as e:
        print(f"[WARNING] Could not save config: {e}")

def list_devices():
    """Print all available audio output devices."""
    print("Available audio devices:")
    for i, dev in enumerate(sd.query_devices()):
        if dev['max_output_channels'] > 0:
            print(f"  [{i}] {dev['name']}")

# list_devices()  # <-- Uncomment this, run once, then comment it back out


# ============================================================
# WAVEFORM GENERATORS
# These functions create the raw audio data for each haptic event
# ============================================================

# def sine_wave(frequency, duration, volume=1.0, fade=True):
    # """
    # Generate a simple sine wave - good for clean pulses and thumps.
    # frequency: Hz (20-80 is ideal for transducers)
    # duration:  seconds
    # volume:    0.0 to 1.0
    # fade:      if True, fades out smoothly to avoid clicks
    # """
    # t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    # wave = np.sin(2 * np.pi * frequency * t) * volume * MASTER_VOLUME

    # if fade:
        ## Apply a smooth fade-out over the last 20% of the duration
        # fade_samples = int(len(wave) * 0.2)
        # fade_curve = np.linspace(1.0, 0.0, fade_samples)
        # wave[-fade_samples:] *= fade_curve

    # return wave.astype(np.float32)

DEVICE_INDEX = None
STREAM = None
audio_queue = queue.Queue()
audio_pending = None
audio_pending_offset = 0
playback_generation = 0
playback_lock = threading.Lock()

# Long-running state effects (fuel scooping, overheating) are generated in
# short chunks so they can stop promptly when the corresponding Status.json
# flag clears. Each named effect owns one lightweight daemon thread.
continuous_effects = {}
continuous_effects_lock = threading.Lock()

def audio_callback(outdata, frames, time, status):
    """Called by sounddevice whenever it needs more audio data."""
    global audio_pending, audio_pending_offset
    data = np.zeros(frames, dtype=np.float32)
    frames_filled = 0

    while frames_filled < frames:
        if audio_pending is None:
            try:
                audio_pending = audio_queue.get_nowait()
                audio_pending_offset = 0
            except queue.Empty:
                break

        remaining = len(audio_pending) - audio_pending_offset
        count = min(remaining, frames - frames_filled)
        data[frames_filled:frames_filled + count] = audio_pending[
            audio_pending_offset:audio_pending_offset + count]
        frames_filled += count
        audio_pending_offset += count
        if audio_pending_offset >= len(audio_pending):
            audio_pending = None
            audio_pending_offset = 0

    outdata[:, 0] = data

def init_audio():
    global DEVICE_INDEX, STREAM
    if STREAM:
        STREAM.stop()
        STREAM.close()
        STREAM = None
    DEVICE_INDEX = None
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if OUTPUT_DEVICE_NAME.lower() in dev['name'].lower():
            if dev['max_output_channels'] > 0:
                DEVICE_INDEX = i
                break
    if DEVICE_INDEX is None:
        print(f"[WARNING] Could not find device: {OUTPUT_DEVICE_NAME}")
        return
    STREAM = sd.OutputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype='float32',
        device=DEVICE_INDEX,
        callback=audio_callback
    )
    STREAM.start()
    print(f"[AUDIO] Stream opened on device {DEVICE_INDEX}: {devices[DEVICE_INDEX]['name']}")

haptic_queue = queue.Queue()
effect_context = threading.local()

def haptic_worker():
    """Single background thread that plays haptics in order."""
    while True:
        item = haptic_queue.get()
        try:
            if isinstance(item, tuple) and len(item) == 3:
                fn, intensity, generation = item
            elif isinstance(item, tuple):
                fn, intensity = item
                generation = playback_generation
            else:
                fn, intensity = item, 1.0
                generation = playback_generation
            effect_context.intensity = intensity
            effect_context.generation = generation
            fn()
        except Exception as e:
            print(f"[ERROR] Haptic playback failed: {e}")
        finally:
            haptic_queue.task_done()

# Start it once at startup
threading.Thread(target=haptic_worker, daemon=True).start()

def queue_haptic(fn, intensity=1.0):
    """Add a haptic function to the playback queue."""
    haptic_queue.put((fn, max(0.0, float(intensity)), playback_generation))

def interrupt_playback():
    """Immediately discard current and queued one-shot playback."""
    global playback_generation, audio_pending, audio_pending_offset
    with playback_lock:
        playback_generation += 1
        audio_pending = None
        audio_pending_offset = 0
        while True:
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                haptic_queue.get_nowait()
                haptic_queue.task_done()
            except queue.Empty:
                break

def sine_wave(frequency, duration, volume=1.0, fade=True, fade_ms=50):
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    wave = np.sin(2 * np.pi * frequency * t) * volume * MASTER_VOLUME
    if fade:
        fade_samples = int(SAMPLE_RATE * fade_ms / 1000)  # fixed ms, not %
        wave[:fade_samples]  *= np.linspace(0.0, 1.0, fade_samples)  # fade in
        wave[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples)  # fade out
    return wave.astype(np.float32)


def layered_wave(frequencies, duration, volume=1.0):
    """
    Mix multiple sine waves together - creates richer, more complex feels.
    Good for things like engine rumble or hyperspace effects.
    frequencies: list of Hz values e.g. [30, 45, 60]
    """
    wave = np.zeros(int(SAMPLE_RATE * duration))
    for freq in frequencies:
        wave += sine_wave(freq, duration, volume / len(frequencies), fade=False)

    # Normalize and apply master volume
    wave = wave / np.max(np.abs(wave)) * volume * MASTER_VOLUME

    # Fade out
    fade_samples = int(len(wave) * 0.15)
    wave[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples)

    return wave.astype(np.float32)


def pulse_burst(frequency, num_pulses, pulse_duration, gap_duration, volume=1.0):
    """
    A series of short pulses with gaps - good for alerts or warnings.
    e.g. shield warning: 3 quick buzzes
    """
    pulse = sine_wave(frequency, pulse_duration, volume, fade=True)
    gap = np.zeros(int(SAMPLE_RATE * gap_duration), dtype=np.float32)
    burst = np.concatenate([np.concatenate([pulse, gap]) for _ in range(num_pulses)])
    return burst.astype(np.float32)

def pulse_burstrand(frequency, num_pulses, pulse_duration, gap_duration, volume=1.0,
                freq_variance=0.12, duration_variance=0.15, gap_variance=0.2, volume_variance=0.1):
    """
    A series of short pulses with gaps - each pulse is slightly randomized
    to feel organic rather than mechanical.

    Variance params (0.0 to 1.0) control how much each property can deviate:
        freq_variance:     how much pitch shifts between pulses  (default ±12%)
        duration_variance: how much pulse length varies          (default ±15%)
        gap_variance:      how much silence between pulses varies(default ±20%)
        volume_variance:   how much each pulse's volume varies   (default ±10%)
    """
    chunks = []
    for _ in range(num_pulses):
        r_freq     = frequency      * np.random.uniform(1 - freq_variance,     1 + freq_variance)
        r_duration = pulse_duration * np.random.uniform(1 - duration_variance, 1 + duration_variance)
        r_gap      = gap_duration   * np.random.uniform(1 - gap_variance,      1 + gap_variance)
        r_volume   = volume         * np.random.uniform(1 - volume_variance,   1 + volume_variance)

        pulse = sine_wave(r_freq, r_duration, r_volume, fade=True)
        gap   = np.zeros(int(SAMPLE_RATE * r_gap), dtype=np.float32)
        chunks.append(np.concatenate([pulse, gap]))

    return np.concatenate(chunks).astype(np.float32)


def rising_wave(start_freq, end_freq, duration, volume=1.0):
    """
    A sine wave that sweeps upward in frequency - perfect for FSD charging.
    Gives a great sense of building energy.
    """
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    # Linearly interpolate frequency over time
    freq = np.linspace(start_freq, end_freq, len(t))
    # Integrate frequency to get phase (prevents clicking)
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    wave  = np.sin(phase) * volume * MASTER_VOLUME

    # Fade in and out
    fade_in  = int(len(wave) * 0.1)
    fade_out = int(len(wave) * 0.15)
    wave[:fade_in]   *= np.linspace(0.0, 1.0, fade_in)
    wave[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)

    return wave.astype(np.float32)


def noise_burst(duration, volume=1.0, lowpass_freq=80):
    """
    Filtered random noise - gives an organic, chaotic feel.
    Good for interdiction or collision effects.
    """
    noise = np.random.uniform(-1, 1, int(SAMPLE_RATE * duration))

    # Simple low-pass filter: smooth out high frequencies
    # (keeps the rumble, removes harsh buzz)
    kernel_size = max(1, int(SAMPLE_RATE / lowpass_freq))
    kernel = np.ones(kernel_size) / kernel_size
    noise  = np.convolve(noise, kernel, mode='same')

    # Normalize
    noise = noise / np.max(np.abs(noise)) * volume * MASTER_VOLUME

    # Fade out
    fade_samples = int(len(noise) * 0.2)
    noise[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples)

    return noise.astype(np.float32)


def load_effect_definitions():
    """Load data-driven effects when the external catalog changes."""
    global EFFECT_DEFINITIONS, EXTERNAL_EFFECTS, EFFECTS_CONFIG_MTIME
    try:
        modified = os.path.getmtime(EFFECTS_CONFIG_PATH)
        if EFFECTS_CONFIG_MTIME == modified:
            return
        with open(EFFECTS_CONFIG_PATH, "r") as file:
            catalog = json.load(file)
        EFFECT_DEFINITIONS = catalog.get("effects", {})
        for effect_id, description in catalog.get("descriptions", {}).items():
            if effect_id in EFFECT_DEFINITIONS:
                EFFECT_DEFINITIONS[effect_id]["description"] = description
        EFFECTS_CONFIG_MTIME = modified
        EXTERNAL_EFFECTS = {}
    except (OSError, json.JSONDecodeError) as error:
        print(f"[WARNING] Could not load effect catalog: {error}")


def render_atmospheric_rumble(definition):
    """Render a configurable layered low-frequency atmospheric effect."""
    duration = float(definition.get("duration", 7.5))
    count = max(1, int(SAMPLE_RATE * duration))
    t = np.arange(count) / SAMPLE_RATE
    wave = np.zeros(count, dtype=np.float64)

    for component in definition.get("components", []):
        amplitude = float(component.get("amplitude", 1.0))
        phase_offset = float(component.get("phase", 0.0))
        if "start_frequency" in component:
            frequencies = np.linspace(float(component["start_frequency"]),
                                      float(component.get("end_frequency", component["start_frequency"])),
                                      count)
            phase = 2 * np.pi * np.cumsum(frequencies) / SAMPLE_RATE + phase_offset
            wave += amplitude * np.sin(phase)
        else:
            frequency = float(component.get("frequency", 30))
            wave += amplitude * np.sin(2 * np.pi * frequency * t + phase_offset)

    modulation = np.ones(count, dtype=np.float64)
    for mod in definition.get("amplitude_modulation", []):
        modulation += float(mod.get("depth", 0.0)) * np.sin(
            2 * np.pi * float(mod.get("rate", 0.2)) * t + float(mod.get("phase", 0.0)))
    wave *= modulation

    noise = definition.get("noise", {})
    noise_amount = float(noise.get("amount", 0.0))
    if noise_amount:
        smoothing = max(1, int(noise.get("smoothing", 900)))
        raw = np.random.uniform(-1, 1, count + smoothing - 1)
        kernel = np.ones(smoothing) / smoothing
        wave += noise_amount * np.convolve(raw, kernel, mode="valid")

    fade_in = min(count, int(SAMPLE_RATE * float(definition.get("fade_in", 1.0))))
    if fade_in:
        wave[:fade_in] *= np.sin(np.linspace(0, np.pi / 2, fade_in)) ** 2
    fade_start = min(count, int(SAMPLE_RATE * float(definition.get("fade_out_start", 5.0))))
    if fade_start < count:
        wave[fade_start:] *= np.cos(np.linspace(0, np.pi / 2, count - fade_start)) ** 2

    peak = np.max(np.abs(wave))
    if peak:
        wave = wave / peak
    wave *= float(definition.get("volume", 0.48)) * MASTER_VOLUME
    return wave.astype(np.float32)


def render_config_effect(effect_id):
    load_effect_definitions()
    definition = EFFECT_DEFINITIONS[effect_id]
    generator = definition.get("generator")
    if generator == "atmospheric_rumble":
        return render_atmospheric_rumble(definition)
    if generator == "sequence":
        variables = resolve_recipe_variables(definition)
        wave_steps = [step for step in definition.get("steps", []) if step.get("type") != "wait"]
        if len(wave_steps) == 1:
            return render_recipe_step(wave_steps[0], variables)
    raise ValueError(f"Unsupported effect generator: {generator}")


def resolve_recipe_value(value, variables=None):
    variables = variables or {}
    if isinstance(value, str) and value.startswith("$"):
        return variables[value[1:]]
    if isinstance(value, dict):
        if "integer" in value:
            return random.randint(int(value["integer"][0]), int(value["integer"][1]))
        if "uniform" in value:
            return random.uniform(float(value["uniform"][0]), float(value["uniform"][1]))
        if "triangular" in value:
            low, high, mode = value["triangular"]
            return random.triangular(float(low), float(high), float(mode))
    return value


def resolve_recipe_variables(definition):
    return {
        name: resolve_recipe_value(value)
        for name, value in definition.get("variables", {}).items()
    }


def render_recipe_step(step, variables=None):
    value = lambda name, default=None: resolve_recipe_value(step.get(name, default), variables)
    step_type = step.get("type")
    if step_type == "sine":
        return sine_wave(float(value("frequency", 40)), float(value("duration", 0.2)),
                         float(value("volume", 1.0)), fade=bool(value("fade", True)))
    if step_type == "layered":
        return layered_wave([float(resolve_recipe_value(item, variables))
                             for item in step.get("frequencies", [])],
                            float(value("duration", 0.5)), float(value("volume", 1.0)))
    if step_type == "sweep":
        return rising_wave(float(value("start_frequency", 30)),
                           float(value("end_frequency", 60)),
                           float(value("duration", 1.0)), float(value("volume", 1.0)))
    if step_type == "noise":
        return noise_burst(float(value("duration", 0.5)), float(value("volume", 1.0)),
                           float(value("lowpass_frequency", 80)))
    if step_type == "random_pulse":
        return pulse_burstrand(
            float(value("frequency", 40)), int(value("count", 1)),
            float(value("pulse_duration", 0.1)), float(value("gap_duration", 0.1)),
            float(value("volume", 1.0)), float(value("frequency_variance", 0.12)),
            float(value("duration_variance", 0.15)), float(value("gap_variance", 0.2)),
            float(value("volume_variance", 0.1)))
    raise ValueError(f"Unsupported recipe step: {step_type}")


def execute_config_effect(effect_id):
    load_effect_definitions()
    definition = EFFECT_DEFINITIONS[effect_id]
    if definition.get("generator") == "atmospheric_rumble":
        play(render_atmospheric_rumble(definition))
        return
    variables = resolve_recipe_variables(definition)
    for step in definition.get("steps", []):
        if step.get("type") == "wait":
            time.sleep(float(resolve_recipe_value(step.get("duration", 0), variables)))
        else:
            play(render_recipe_step(step, variables))


def get_external_effect_registry(include_internal=True):
    load_effect_definitions()
    for effect_id in EFFECT_DEFINITIONS:
        if effect_id not in EXTERNAL_EFFECTS:
            def effect(effect_id=effect_id):
                printAction(EFFECT_DEFINITIONS[effect_id].get("name", effect_id))
                execute_config_effect(effect_id)
            effect.__name__ = f"haptic_{effect_id}"
            EXTERNAL_EFFECTS[effect_id] = effect
    if include_internal:
        return dict(EXTERNAL_EFFECTS)
    return {
        effect_id: effect for effect_id, effect in EXTERNAL_EFFECTS.items()
        if EFFECT_DEFINITIONS.get(effect_id, {}).get("selectable", True)
    }


# ============================================================
# EFFECT PLAYBACK RUNTIME
# Effect recipes and their descriptions are loaded from effects.json.
# ============================================================

def play(wave):
    """Queue a wave for playback - non-blocking, no stream open/close."""
    if getattr(effect_context, "generation", playback_generation) != playback_generation:
        return
    intensity = getattr(effect_context, "intensity", 1.0)
    audio_queue.put((wave * intensity).astype(np.float32))

def start_continuous_effect(name, wave_factory, interval=0.35):
    """Start a repeatable haptic bed unless it is already running."""
    with continuous_effects_lock:
        current = continuous_effects.get(name)
        if current and current[0].is_alive():
            return

        stop_event = threading.Event()
        intensity = getattr(effect_context, "intensity", 1.0)

        def worker():
            effect_context.intensity = intensity
            while not stop_event.is_set():
                effect_context.generation = playback_generation
                play(wave_factory())
                stop_event.wait(interval)

        thread = threading.Thread(target=worker, daemon=True, name=f"haptic-{name}")
        continuous_effects[name] = (thread, stop_event)
        thread.start()

def stop_continuous_effect(name):
    """Stop a running state effect. Already-buffered audio may finish."""
    with continuous_effects_lock:
        current = continuous_effects.pop(name, None)
    if current:
        current[1].set()

def printAction(action):
    print("[HAPTIC] " + action + "\n")

# ============================================================
# EFFECT / TRIGGER REGISTRY
# ============================================================

def get_effect_registry():
    """Return user-selectable effects from the external recipe catalog."""
    return get_external_effect_registry(include_internal=False)

def get_effect_display_name(effect_id):
    load_effect_definitions()
    definition = EFFECT_DEFINITIONS.get(effect_id, {})
    return definition.get("name")

def dispatch_trigger(trigger, default_method=None):
    """Play a default-config mapping plus any user-added mappings."""
    emit_monitor_event("App trigger", trigger)
    load_default_config()
    default = next((item for item in DEFAULT_CONFIG.get("bindings", [])
                    if item.get("trigger") == trigger), None)
    if default:
        binding = dict(default)
        binding.update(BUILTIN_OVERRIDES.get(trigger, {}))
        execute_default_binding(binding)

    registry = get_effect_registry()
    for binding in list(CUSTOM_BINDINGS):
        if not binding.get("enabled", True) or binding.get("trigger") != trigger:
            continue
        effect = registry.get(binding.get("effect", ""))
        if effect:
            emit_monitor_event(
                "Effect", get_effect_display_name(binding.get("effect")) or binding.get("effect", ""),
                f"Custom · strength {float(binding.get('intensity', 1.0)):.2f}")
            queue_haptic(effect, binding.get("intensity", 1.0))

def execute_default_binding(binding):
    action = binding.get("action", "play")
    effect_id = binding.get("effect", "")
    intensity = binding.get("intensity", 1.0)
    enabled = binding.get("enabled", True)
    all_effects = get_external_effect_registry(include_internal=True)

    if action == "continuous_stop":
        emit_monitor_event("Effect", get_effect_display_name(effect_id) or effect_id, "Stopped")
        stop_continuous_effect(binding.get("state", effect_id))
        return
    if not enabled:
        return
    if action == "continuous_start":
        state = binding.get("state", effect_id)
        interval = float(binding.get("interval", 0.4))
        def start():
            start_continuous_effect(
                state, lambda: render_config_effect(effect_id), interval=interval)
        emit_monitor_event("Effect", get_effect_display_name(effect_id) or effect_id,
                           f"Continuous · strength {float(intensity):.2f}")
        queue_haptic(start, intensity)
        return
    if action == "shield_aware_attack":
        selected = "under_attack_shields" if CURRENT_STATUS_FLAGS & (1 << 3) else "under_attack_hull"
        emit_monitor_event("Effect", get_effect_display_name(selected) or selected,
                           f"Adaptive · strength {float(intensity):.2f}")
        queue_haptic(all_effects[selected], intensity)
        return
    if action == "hull_threshold":
        if CURRENT_HULL_HEALTH > 0.70:
            selected = "hull_threshold_80"
        elif CURRENT_HULL_HEALTH > 0.50:
            selected = "hull_threshold_60"
        elif CURRENT_HULL_HEALTH > 0.30:
            selected = "hull_threshold_40"
        else:
            selected = "hull_threshold_20"
        emit_monitor_event("Effect", get_effect_display_name(selected) or selected,
                           f"Adaptive · strength {float(intensity):.2f}")
        queue_haptic(all_effects[selected], intensity)
        return
    effect = all_effects.get(effect_id)
    if effect:
        emit_monitor_event("Effect", get_effect_display_name(effect_id) or effect_id,
                           f"Default · strength {float(intensity):.2f}")
        queue_haptic(effect, intensity)

def get_builtin_bindings():
    """Describe shipped mappings from default_config.json for the UI."""
    load_default_config()
    result = []
    for default in DEFAULT_CONFIG.get("bindings", []):
        binding = dict(default)
        binding.update(BUILTIN_OVERRIDES.get(binding.get("trigger"), {}))
        binding["control"] = binding.get("action") in ("continuous_start", "continuous_stop")
        binding["source"] = "Default"
        result.append(binding)
    return result

# ============================================================
# STATUS.JSON WATCHER
# Status.json updates in realtime and tracks shield/hull state
# Watch for state changes rather than events
# ============================================================

class StatusWatcher:
    """
    Polls Status.json for state changes.
    Detects shield loss, hull damage thresholds, etc.
    """
    def __init__(self):
        self.last_status = {}
        self.status_path = os.path.join(JOURNAL_DIR, "Status.json")

    def check(self):
        """Read Status.json and react to any changes."""
        global CURRENT_STATUS_FLAGS
        try:
            with open(self.status_path, 'r') as f:
                content = f.read().strip()
                if not content:
                    return
                status = json.loads(content)

            # --- Shield state change ---
            # The first read establishes a baseline. Without this guard, every
            # currently-enabled ship state feels like a new transition when
            # the application starts.
            if not self.last_status:
                self.last_status = status
                CURRENT_STATUS_FLAGS = status.get("Flags", 0)
                return

            old_flags = self.last_status.get("Flags", 0)
            new_flags = status.get("Flags", 0)
            CURRENT_STATUS_FLAGS = new_flags

            for bit, on_trigger, off_trigger in FLAG_WATCHERS:
                mask = (1 << bit)
                old_state = bool(old_flags & mask)
                new_state = bool(new_flags & mask)

                if old_state != new_state:
                    trigger = on_trigger if new_state else off_trigger
                    emit_monitor_event("Status", trigger)
                    if new_state:
                        dispatch_trigger(on_trigger)
                    else:
                        dispatch_trigger(off_trigger)

            self.last_status = status

        except (FileNotFoundError, json.JSONDecodeError):
            pass  # File may be mid-write, just wait for next poll

# ============================================================
# JOURNAL WATCHER
# Watches the latest journal file for new events line-by-line
# ============================================================

class JournalHandler(FileSystemEventHandler):
    """Watchdog handler that reacts to journal file changes."""

    def __init__(self):
        self.journal_path = self._get_latest_journal()
        self.file_position = 0  # Track where we've read up to

        # Seek to end of file on startup so we only catch NEW events
        if self.journal_path:
            with open(self.journal_path, 'r') as f:
                f.seek(0, 2)  # Seek to end
                self.file_position = f.tell()
            print(f"[INFO] Watching journal: {os.path.basename(self.journal_path)}")
        else:
            print("[WARNING] No journal file found. Start Elite Dangerous first,")
            print("          or check your JOURNAL_DIR path.")

    def _get_latest_journal(self):
        """Find the most recently modified journal file."""
        pattern = os.path.join(JOURNAL_DIR, "Journal.*.log")
        files = glob.glob(pattern)
        if not files:
            return None
        return max(files, key=os.path.getmtime)

    def on_modified(self, event):
        """Called by watchdog when any file in the journal dir changes."""
        if event.src_path == self.journal_path:
            self._read_new_lines()
        # If a new journal file appears (e.g. game restart), switch to it
        elif event.src_path.endswith('.log') and 'Journal.' in event.src_path:
            new_journal = self._get_latest_journal()
            if new_journal != self.journal_path:
                print(f"[INFO] Switching to new journal: {os.path.basename(new_journal)}")
                self.journal_path = new_journal
                self.file_position = 0

    def _read_new_lines(self):
        """Read and process any new lines added to the journal."""
        try:
            with open(self.journal_path, 'r') as f:
                f.seek(self.file_position)
                new_lines = f.readlines()
                self.file_position = f.tell()

            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    event_name = event.get("event", "")
                    if event_name:
                        emit_monitor_event("Journal", event_name)

                    trigger = EVENT_MAP.get(event_name, event_name)
                    if event_name == "HullDamage":
                        global CURRENT_HULL_HEALTH
                        CURRENT_HULL_HEALTH = float(event.get("Health", CURRENT_HULL_HEALTH))
                    if event_name in EVENT_MAP or any(
                        b.get("trigger") == trigger and b.get("enabled", True)
                        for b in CUSTOM_BINDINGS
                    ):
                        dispatch_trigger(trigger)

                except json.JSONDecodeError:
                    pass  # Partial write, skip

        except FileNotFoundError:
            pass


