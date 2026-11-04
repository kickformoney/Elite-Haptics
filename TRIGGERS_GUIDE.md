# Elite Haptics trigger catalog

`triggers.json` defines which Elite Dangerous telemetry inputs appear in the Effects tab. Save changes and choose **Settings > Reload triggers** to apply them without restarting the app.

## Adding a Journal event

Add the exact Elite Dangerous event name to `journal_events` and map it to a unique, stable trigger ID:

```json
"SupercruiseExit": "travel_supercruise_exit"
```

Then add the display information under `triggers`:

```json
"travel_supercruise_exit": {
  "category": "Travel",
  "name": "Supercruise - Exit"
}
```

After reloading, the trigger appears in the Effects tab and can be assigned to any selectable effect.

## Trigger metadata

- `category` controls grouping and filtering in the Effects tab.
- `name` is the friendly label shown to users.
- `annotation: "special"` adds the coral special-trigger treatment.
- `annotation: "advanced"` adds a normal-color asterisk for Advanced Maintenance.
- `note` supplies the explanatory text shown below the controls.

Annotations are presentation metadata. They do not create special playback logic.

## Status flags

`status_flags` watches bit transitions in Elite Dangerous `Status.json`:

```json
{
  "bit": 3,
  "on": "ship_shields_restored",
  "off": "ship_shields_offline"
}
```

Entries use the original `Flags` value by default. For Odyssey/on-foot states and
other second-field flags, specify the source explicitly:

```json
{
  "field": "Flags2",
  "bit": 6,
  "on": "onfoot_low_oxygen",
  "off": "onfoot_oxygen_normal"
}
```

Both the `on` and `off` trigger IDs need entries under `triggers`. Only add or change a Status bit after confirming it against the Elite Dangerous Status Flags documentation.

## Special actions

The catalog can define and display telemetry triggers, but complex behaviors still require an action supported by the engine and assigned in `default_config.json`. Current examples include:

- Continuous start and automatic stop
- Shield-aware Under Attack feedback
- Hull-integrity threshold selection

Ordinary Journal and Status triggers do not require Python changes.
