# Elite Haptics effect catalog

`effects.default.json` contains the built-in catalog shipped with Elite Haptics. `effects.json` is the update-safe user override catalog. Add only new effects or built-in effects you want to replace to `effects.json`; matching IDs override the shipped definition at runtime.

Keep `effects.json` as valid JSON: property names and text require double quotes, commas separate entries, and JSON does not support comments. Application updates replace `effects.default.json` but preserve `effects.json`.

Before an update changes the strength of a built-in effect, Elite Haptics backs up `haptics_config.json` and applies any release-provided compensation to customized mappings and Test Pad assignments. Migrated strengths are rounded to the nearest `0.05`. Effects overridden in `effects.json` are never automatically compensated because their built-in waveform is not being used.

## Safest way to add an effect

1. Make a backup of `effects.json`.
2. Duplicate a similar entry from `effects.default.json` inside the `effects` object in `effects.json`.
3. Give a new effect a unique ID, such as `my_docking_clamp`. To override a built-in effect, keep its existing ID.
4. Change its `name`, `category`, `description`, and recipe values.
5. Save the file and choose **Settings > Reload effects**.
6. Test it on the Test Pad, then assign it to a telemetry trigger on the Effects tab.

Example:

```json
"my_test_bump": {
  "name": "My Test Bump",
  "category": "Ship",
  "description": "A short test bump for experimenting with the effect editor.",
  "generator": "sequence",
  "steps": [
    {
      "type": "sine",
      "frequency": 40,
      "duration": 0.25,
      "volume": 0.5
    }
  ]
}
```

## Sequence steps

- `sine`: one frequency. Uses `frequency`, `duration`, `volume`, and optional `fade`.
- `layered`: simultaneous frequencies. Uses `frequencies`, `duration`, and `volume`.
- `sweep`: moves between frequencies. Uses `start_frequency`, `end_frequency`, `duration`, and `volume`.
- `noise`: filtered noise. Uses `duration`, `volume`, and `lowpass_frequency`.
- `random_pulse`: irregular impacts. Uses `frequency`, `count`, `pulse_duration`, `gap_duration`, and `volume`. Optional variance properties are `frequency_variance`, `duration_variance`, `gap_variance`, and `volume_variance`.
- `wait`: delays the next step. Uses `duration`.

For bass shakers, frequencies around 20–80 Hz are generally the most useful. Start at a low volume and increase it gradually.

## Random values and variables

A numeric property can choose a new value each time the effect runs:

```json
"frequency": { "integer": [30, 40] }
"duration": { "uniform": [0.2, 0.4] }
```

Reusable random values can be declared in `variables` and referenced with `$name`. Existing repair effects contain complete examples, including triangular distributions.

## Other properties

- `selectable: false` hides an internal helper effect from user-facing effect lists.
- `generator: "atmospheric_rumble"` creates the specialized Leave Body effects. Duplicate one of those recipes when making another atmospheric effect.
- Effect IDs are configuration keys. Renaming an ID breaks mappings that still refer to the old ID; changing only `name` is safe.

If reloading fails, the app keeps its previously loaded catalog and displays the JSON or validation error.
