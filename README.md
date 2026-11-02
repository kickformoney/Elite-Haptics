# Elite Haptics

Elite Haptics is an unofficial Windows application that turns Elite Dangerous Player Journal and Status events into configurable tactile feedback for bass shakers and other transducers.

Instead of converting all game audio into vibration, Elite Haptics gives supported events their own physical character. FSD charging, docking clamps, shield changes, interdictions, landing gear, fuel scooping, station services, SRV activity, heat warnings, and other events can each produce a distinct effect.

## Features

- Editable telemetry-to-effect mappings
- Independent strength controls for each mapping
- Multiple effects per telemetry trigger
- Configurable NumPad Test Pad modes
- Live journal, trigger, and effect monitoring
- User-editable JSON effect recipes with live reload
- User-editable Journal and Status trigger catalogs with live reload
- Continuous feedback for fuel scooping and overheating
- Light and dark themes
- Selectable Windows audio output
- Default mappings that remain visible and editable

## How it works

Elite Haptics watches the local Elite Dangerous Player Journal and `Status.json`. Supported events are translated into low-frequency audio waveforms and sent to the Windows output device selected in Settings. That output can be routed to a bass-shaker amplifier directly or through software such as VoiceMeeter.

The application does not modify the game, inject code, read game memory, or transmit journal data over the internet.

## Requirements

- Windows 10 or Windows 11
- A bass shaker or tactile transducer with a suitable amplifier
- A dedicated or appropriately routed Windows audio output
- Elite Dangerous journal files

Start with low amplifier and application levels. Increase them gradually to protect the transducer, amplifier, furniture, and listener.

## Running a release

1. Download and extract the release ZIP.
2. Run `elite-haptics.exe`.
3. Open Settings and select the audio device connected to the transducer.
4. Confirm the Elite Dangerous journal directory.
5. Use the Test Pad at low strength before starting the game.

User preferences are written to `haptics_config.json` beside the executable. This file is intentionally excluded from source control because it can contain a Windows username, local paths, audio-device choices, and personal effect tuning.

## Running from source

Install 64-bit Python 3.12, open PowerShell in the project directory, and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe elite_transducers_ui.py
```

## Building the Windows executable

Run:

```powershell
.\build-ui.cmd
```

The script creates a local virtual environment when needed, installs the pinned build dependencies, and writes the distributable files to `dist`.

To build manually:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm elite-transducers-ui.spec
```

PyInstaller is not a cross-compiler. Build the Windows executable on Windows.

## Creating a release

The release script builds the application, verifies and packages the distributable files, and can publish the result through the GitHub CLI. Install and authenticate the CLI once with `gh auth login` before publishing.

To build a ZIP locally without changing anything on GitHub:

```powershell
.\release.ps1 0.1.0 -PackageOnly
```

To publish a preview release:

```powershell
.\release.ps1 0.1.0 -Prerelease
```

For a normal release, omit `-Prerelease`. By default, GitHub generates release notes from the repository history. Supply edited notes with `-NotesFile .\release-notes.md`. The script asks for confirmation immediately before pushing and publishing; `-Yes` skips that question for unattended use.

## Editing and adding effects

The working effect catalog is `effects.json`. In the application, use **Settings > Open effects.json**, save changes, then choose **Reload effects**. New selectable effects become available in the Test Pad and mapping controls.

See [EFFECTS_GUIDE.md](EFFECTS_GUIDE.md) for the recipe format, supported waveform steps, random values, and an example effect.

Trigger names, categories, Journal event mappings, and supported Status flag transitions are stored in `triggers.json`. It can be opened and reloaded from Settings. See [TRIGGERS_GUIDE.md](TRIGGERS_GUIDE.md) for examples and schema details. A future visual Trigger Manager is planned; special engine actions still require supported action types in the application code.

## Telemetry limitations

Elite Dangerous does not journal every physical action. An effect can only trigger automatically when a suitable Player Journal event or Status flag is available. The Monitoring tab helps identify incoming events and distinguish them from effects dispatched by the application.

## Project status

Elite Haptics is an early community release. Effect timing and intensity can vary considerably with the transducer, amplifier, mounting, crossover, and chair or cockpit construction. Feedback and additional effect recipes are welcome.

## License

Elite Haptics is available under the [MIT License](LICENSE). You may use, modify, and distribute it for private or commercial purposes, provided the copyright and license notice are retained.

## Disclaimer

Elite Haptics is an unofficial community project and is not affiliated with or endorsed by Frontier Developments. Elite Dangerous and related names are trademarks of their respective owners.
