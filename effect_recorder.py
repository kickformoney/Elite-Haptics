"""Pure timing and serialization helpers for the effect recorder."""

from __future__ import annotations

import re


MIN_DURATION = 0.03
DRAG_THRESHOLD = 0.08
STRENGTH_THRESHOLD = 0.06
SAMPLE_INTERVAL = 0.06


def pad_values(x, y, width, height):
    """Map a pad coordinate to (frequency, strength), clamped to its bounds."""
    width = max(float(width), 1.0)
    height = max(float(height), 1.0)
    x_ratio = min(1.0, max(0.0, float(x) / width))
    y_ratio = min(1.0, max(0.0, float(y) / height))
    return round(120.0 - (100.0 * y_ratio), 1), round(0.1 + (0.9 * x_ratio), 2)


def make_step(start, end, duration, strength, threshold=DRAG_THRESHOLD):
    """Create a sine or sweep step from a completed press."""
    duration = round(max(MIN_DURATION, float(duration)), 3)
    strength = round(min(1.0, max(0.0, float(strength))), 2)
    start = round(float(start), 1)
    end = round(float(end), 1)
    relative_motion = abs(end - start) / 100.0
    if relative_motion >= threshold:
        return {
            "type": "sweep", "start_frequency": start, "end_frequency": end,
            "duration": duration, "volume": strength,
        }
    step = {
        "type": "sine", "frequency": round((start + end) / 2.0, 1),
        "duration": duration, "volume": strength,
    }
    # The engine's fixed 50 ms fade is longer than very quick taps.
    if duration < 0.05:
        step["fade"] = False
    return step


def append_press(steps, pressed_at, released_at, start_frequency, end_frequency,
                 strength, previous_release=None):
    """Append inter-press timing and a completed gesture to a sequence."""
    result = list(steps)
    if previous_release is not None:
        gap = float(pressed_at) - float(previous_release)
        if gap >= MIN_DURATION:
            result.append({"type": "wait", "duration": round(gap, 3)})
    result.append(make_step(start_frequency, end_frequency,
                            float(released_at) - float(pressed_at), strength))
    return result


def gesture_steps(samples):
    """Turn timestamped frequency/strength samples into meaningful segments."""
    if len(samples) < 2:
        return []
    cleaned = [tuple(map(float, samples[0]))]
    for sample in samples[1:-1]:
        sample = tuple(map(float, sample))
        previous = cleaned[-1]
        if (sample[0] - previous[0] >= SAMPLE_INTERVAL and
                (abs(sample[1] - previous[1]) >= 3.0 or
                 abs(sample[2] - previous[2]) >= 0.04)):
            cleaned.append(sample)
    final = tuple(map(float, samples[-1]))
    if final[0] > cleaned[-1][0]:
        cleaned.append(final)

    frequency_range = max(item[1] for item in cleaned) - min(item[1] for item in cleaned)
    strength_range = max(item[2] for item in cleaned) - min(item[2] for item in cleaned)
    duration = cleaned[-1][0] - cleaned[0][0]
    if frequency_range / 100.0 < DRAG_THRESHOLD and strength_range < STRENGTH_THRESHOLD:
        average_strength = sum(item[2] for item in cleaned) / len(cleaned)
        return [make_step(cleaned[0][1], cleaned[-1][1], duration, average_strength)]

    result = []
    for start, end in zip(cleaned, cleaned[1:]):
        segment_duration = end[0] - start[0]
        if segment_duration <= 0:
            continue
        result.append(make_step(start[1], end[1], segment_duration,
                                (start[2] + end[2]) / 2.0, threshold=0.02))
    return result


def append_gesture(steps, samples, previous_release=None):
    """Append an inter-press wait and a sampled gesture path."""
    if len(samples) < 2:
        return list(steps)
    result = list(steps)
    pressed_at = float(samples[0][0])
    if previous_release is not None:
        gap = pressed_at - float(previous_release)
        if gap >= MIN_DURATION:
            result.append({"type": "wait", "duration": round(gap, 3)})
    result.extend(gesture_steps(samples))
    return result


def trim_silence(steps):
    """Remove leading and trailing waits without altering internal rhythm."""
    result = list(steps)
    while result and result[0].get("type") == "wait":
        result.pop(0)
    while result and result[-1].get("type") == "wait":
        result.pop()
    return result


def unique_effect_id(name, existing_ids):
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "custom_effect"
    candidate = f"custom_{base}" if not base.startswith("custom_") else base
    suffix = 2
    while candidate in existing_ids:
        candidate = f"custom_{base}_{suffix}"
        suffix += 1
    return candidate


def effect_definition(name, category, description, steps):
    if not name.strip():
        raise ValueError("Name is required.")
    if not category.strip():
        raise ValueError("Category is required.")
    steps = trim_silence(steps)
    if not steps or not any(step.get("type") != "wait" for step in steps):
        raise ValueError("Record at least one press before saving.")
    return {
        "name": name.strip(), "category": category.strip(),
        "description": description.strip(), "generator": "sequence", "steps": steps,
    }


def validate_sequence_steps(steps):
    """Validate editable recorder steps without requiring the audio engine."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("Steps must be a non-empty JSON array.")
    required = {
        "sine": ("frequency", "duration", "volume"),
        "wait": ("duration",),
        "sweep": ("start_frequency", "end_frequency", "duration", "volume"),
    }
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ValueError(f"Step {index} must be a JSON object.")
        step_type = step.get("type")
        if step_type not in required:
            raise ValueError(f"Step {index} has unsupported type '{step_type}'.")
        for field in required[step_type]:
            try:
                value = float(step[field])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"Step {index} needs a numeric '{field}'.") from None
            if field == "duration" and value <= 0:
                raise ValueError(f"Step {index} duration must be greater than zero.")
            if field == "volume" and not 0 <= value <= 1:
                raise ValueError(f"Step {index} volume must be between 0 and 1.")
    return steps
