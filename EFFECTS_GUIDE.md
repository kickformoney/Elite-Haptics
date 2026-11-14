# Elite Haptics effect guide

Elite Haptics includes visual tools for creating, editing, testing, duplicating, renaming, and deleting effects. You normally do not need to edit JSON by hand.

> Start with a low master strength while designing an effect. Bass shakers can become unexpectedly powerful at some frequencies.

## Record a new effect

1. Open **Settings** and select **Record effect…**.
2. Enter a unique name. The Effect ID is generated from the name and can be changed later.
3. Select **Record**, then drag inside the pad. Left to right controls strength; bottom to top controls frequency.
4. Use **Pause**, **Preview**, **Stop**, **Clear**, and **Trim silence** as needed.
5. Select **Save effect**. If the name already exists, the app asks you to choose another one.

The recorder turns horizontal or diagonal movement into frequency sweeps. The reticle and readouts update while you drag, and preview uses the same strength calculation as the finished effect.

## Edit an effect visually

Open **Settings > Manage effects…**, select one of your effects, and choose **Edit**.

The upper timeline controls frequency and the lower timeline controls strength. The shared playhead moves through both lanes during playback.

- Select a section or point to edit its exact values.
- Drag points to reshape an effect.
- Click a lane to split a section at that time.
- Merge sections when separate points are no longer useful.
- Turn on **Link nearby frequency points** to move frequency points that are visually close together. Strength remains independently adjustable in every section.
- Use the playback controls to preview the effect while editing.
- Select **Save effect** to keep the changes.

The visual editor supports sequence effects made from sine, sweep, and wait steps. Effects using specialized generators or step types can still be edited as JSON.

## Edit the JSON from the editor

Select **Edit JSON…** in the visual editor to view the complete effect definition. Choose **Apply to timeline** to bring valid changes back into the visual editor, or **Save effect** to save the JSON directly.

Invalid JSON or unsupported values are explained without replacing the last valid version of the effect.

## Manage custom effects

In **Settings > Manage effects…**, you can:

- Preview an effect.
- Edit its timeline, details, or JSON.
- Duplicate it as a starting point for a new effect.
- Change its Effect ID. Existing mappings and Test Pad assignments are updated to use the new ID.
- Delete it after confirmation.

The display name and Effect ID are different. Renaming the display name is safe. If an old ID no longer describes the effect, use **Change ID…** instead of editing the catalog manually.

## Built-in effects and user effects

`effects.default.json` is the built-in catalog shipped to every user. `effects.json` contains update-safe user effects and overrides on this computer.

- Recording, duplicating, or editing an effect in the app saves it to `effects.json`.
- A user effect with the same ID as a built-in effect replaces that built-in definition at runtime.
- Application updates replace `effects.default.json` but preserve `effects.json`.
- To make an effect a new default for every user, update `effects.default.json` as part of a release and prepare the matching effect migration.

Use **Settings > Reload effects** after changing either catalog outside the app. If loading fails, the app keeps the previously loaded catalog and shows the validation error.

## JSON format

The easiest starting point is to duplicate a similar effect in the app, then select **Edit JSON…**. A basic effect looks like this:

```json
{
  "name": "My Test Bump",
  "category": "Ship",
  "description": "A short test bump.",
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

Keep catalog files as valid JSON: property names and text require double quotes, commas separate entries, and JSON does not support comments.

## Sequence step reference

- `sine`: one frequency. Uses `frequency`, `duration`, `volume`, and optional `fade`.
- `sweep`: moves between frequencies. Uses `start_frequency`, `end_frequency`, `duration`, and `volume`.
- `wait`: delays the next step. Uses `duration`.
- `layered`: simultaneous frequencies. Uses `frequencies`, `duration`, and `volume`.
- `noise`: filtered noise. Uses `duration`, `volume`, and `lowpass_frequency`.
- `random_pulse`: irregular impacts. Uses `frequency`, `count`, `pulse_duration`, `gap_duration`, and `volume`. Optional variance properties include `frequency_variance`, `duration_variance`, `gap_variance`, and `volume_variance`.

For bass shakers, frequencies around 20–80 Hz are generally the most useful.

## Random values and advanced effects

A numeric property can choose a new value each time the effect runs:

```json
"frequency": { "integer": [30, 40] }
"duration": { "uniform": [0.2, 0.4] }
```

Reusable random values can be declared in `variables` and referenced with `$name`. Existing repair effects contain complete examples, including triangular distributions.

- `selectable: false` hides an internal helper effect from user-facing lists.
- `generator: "atmospheric_rumble"` creates the specialized Leave Body effects. Duplicate one of those recipes when making another atmospheric effect.
