# Elite Haptics trigger guide

Triggers connect Elite Dangerous telemetry to haptic effects.  Users can assign, adjust, enable, and disable mappings from the **Effects** tab without editing JSON

## Assign an effect to a trigger

1.  Open the **Effects** tab
2.  Select a trigger and an effect
3.  Set the mapping strength
4.  Select **Add binding**

Double-click an existing row to edit it.  The context menu can enable or disable mappings without deleting them

The same trigger can have more than one effect assigned to it.  Built-in mappings and custom mappings are displayed together, with their current enabled state

## Create a new trigger from a Journal event

You can create a new trigger directly from an event name found in the journal file.  Find the value of the `event` property and type it exactly as shown, including capitalization

For example, the journal file may contain an entry for docking clearance granted:

```json
{
  "timestamp": "2026-11-14T18:42:10Z",
  "event": "DockingGranted",
  "LandingPad": 12,
  "StationName": "Example Station"
}
```

To add a haptic effect whenever that event shows up in the journal:

1.  Open the **Effects** tab
2.  Click the **Trigger** box and type `DockingGranted`
3.  Select the effect you wish to play when docking clearance is granted
4.  Set the mapping strength
5.  Click **Add binding**

The new custom mapping will appear in the list, and the assigned effect will play the next time a station grants docking clearance

This direct method works for ordinary journal events that are not already renamed by `triggers.json`.  If an event already appears in the Trigger list, select its existing entry, instead of adding the raw journal event

## Test a mapping

Use the **Test Pad** to preview an effect and adjust its strength before assigning it.  The **Monitoring** tab shows incoming telemetry and can help confirm whether the expected trigger is firing

Changes made in the app are saved in `haptics_config.json`

## Customize the trigger catalog

`triggers.json` defines the trigger names, categories, Journal event mappings, and Status flag transitions available in the app.  Open it from **Settings > Open triggers.json**, save the file, then select **Reload triggers**

If reloading fails, the app keeps the previously loaded catalog and shows the JSON or validation error

Keep `triggers.json` as valid JSON: property names and text require double quotes, commas separate entries, and JSON does not support comments

## Add a Journal event

Add the exact Elite Dangerous event name to `journal_events` and map it to a unique, stable trigger ID

```json
"SupercruiseExit": "travel_supercruise_exit"
```

Then add the display information under `triggers`

```json
"travel_supercruise_exit": {
  "category": "Travel",
  "name": "Supercruise - Exit"
}
```

After reloading, the trigger appears in the **Effects** tab and can be assigned to any selectable effect

## Trigger metadata

- `category` controls grouping and filtering in the Effects tab
- `name` is the friendly label shown to users
- `annotation: "special"` adds the coral special-trigger treatment
- `annotation: "advanced"` adds a normal-color asterisk for Advanced Maintenance
- `note` supplies the explanatory text shown below the controls

Annotations are presentation metadata.  They do not create special playback logic

## Status flags

`status_flags` watches bit transitions in Elite Dangerous `Status.json`

```json
{
  "bit": 3,
  "on": "ship_shields_restored",
  "off": "ship_shields_offline"
}
```

Entries use the original `Flags` value by default.  For Odyssey, on-foot states, and other second-field flags, specify the source explicitly

```json
{
  "field": "Flags2",
  "bit": 6,
  "on": "onfoot_low_oxygen",
  "off": "onfoot_oxygen_normal"
}
```

Both the `on` and `off` trigger IDs need entries under `triggers`.  Only add or change a Status bit after confirming it against the Elite Dangerous Status Flags documentation

## Special actions

The catalog can define and display telemetry triggers, but complex behaviors require an action supported by the engine and assigned in `default_config.json`.  Current examples include:

- Continuous start and automatic stop
- Shield-aware Under Attack feedback
- Hull-integrity threshold selection

Ordinary Journal and Status triggers do not require Python changes

## Moving to a new installation

Select **Settings > Migrate configuration** to copy your settings, mappings, custom effects, and customized trigger catalog from an old install directory to a new one

The migration validates all source files and creates timestamped backups of configuration files that already exist in the new directory