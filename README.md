# Elite Haptics

Elite Haptics is a third-party, unofficial, tool for Elite Dangerous that utilizes the Elite Dangerous journal files to turn events into configurable haptic feedback for bass shakers/tactile transducers

Made to be complementary with apps like Voicemeeter or Simhub, Elite Haptics provides additional haptic feedback to supported events using the game's available telemetry

FSD charging, docking clamps, shield status, interdictions, landing gear, fuel scooping, station services, heat warnings, and other events each have their own custom effects, and using the built-in effect recorder, you can easily add your own custom haptic effects to the game

Effects tab:
<img width="1163" height="1014" alt="image" src="https://github.com/user-attachments/assets/592a47ce-65f6-4a40-8cef-dfd22bf3fd36" />


## Features

- Editable telemetry-to-effect mappings
- Independent strength controls for each mapping
- Optionally, multiple effects per telemetry trigger
- Configurable Test Pad to test effects
- Live journal, trigger, and effect monitoring
- User-editable effect recipes
- User-editable Journal and Status trigger catalogs
- Continuous feedback for events like fuel scooping and overheating
- Selectable Windows audio output

## How it works

Elite Haptics watches the local Elite Dangerous player Journal and `Status.json` files. Supported events are translated into low-frequency audio waveforms and sent to the output device selected in Settings. That output can be routed to a tactile transducer directly, or through software such as VoiceMeeter

The application does not modify the game in any way, inject code, read game memory, or transmit journal data over the internet

## Requirements

- Windows 10 or Windows 11
- A bass shaker/tactile transducer with a suitable amplifier
- A dedicated or appropriately routed Windows audio output, such as with Voicemeeter
- Elite Dangerous

The application defaults to 25% strength.  Start with low amplifier and application levels. Increase them gradually to protect the transducer, amplifier, furniture, and listener

## Running a release

1. Download and extract the release ZIP
2. Run `elite-haptics.exe`
3. Open Settings and select the audio device connected to the transducer
4. Confirm the Elite Dangerous journal directory
5. Use the Test Pad at low strength before testing in game
6. Use the Migration Configuration option in the Settings tab to migrate a previous configuration, if necessary

User preferences can be migrated to a new version using the migration function in the Settings tab

## Running from source

Install 64-bit Python 3.12, open PowerShell in the project directory, and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe elite_haptics_ui.py
```

## Building the Windows executable

Run:

```powershell
.\build-ui.cmd
```

The script creates a local virtual environment when needed, installs the pinned build dependencies, and writes the distributable files to `dist`

To build manually:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm elite-haptics-ui.spec
```

## Creating a release

The release script builds the application, verifies and packages the distributable files, and can publish the result through the GitHub CLI. Install and authenticate the CLI once with `gh auth login` before publishing

To build a ZIP locally without changing anything on GitHub:

```powershell
.\release.ps1 1.0.0 -PackageOnly
```

To publish a preview release:

```powershell
.\release.ps1 1.0.0 -Prerelease
```

For a normal release, omit `-Prerelease`

## Editing and adding effects

### Using the Effect Recorder

From the Settings tab, click Record Effect:
1. Click Record to start
2. Click and drag the mouse in the box - The vertical axis controls frequency, and the horizontal axis controls intensity
3. To add a pause, release the mouse button and click again to resume
4. Click Stop to finish the recording
5. Optionally, trim leading/trailing silence

<img width="879" height="684" alt="image" src="https://github.com/user-attachments/assets/c49a3de8-d7b4-4230-b983-4fc64a01ebd9" /><br/>

Recorded effects can be edited after creation.  Click Manage Effects, select an effect, and click Edit Selected to modify the waveform and strength of each step:
<img width="1112" height="805" alt="image" src="https://github.com/user-attachments/assets/37ebd809-6eca-4305-a6cc-faab7195c547" />

### Modifying in JSON

Built-in effects are stored in `effects.default.json`; update-safe user additions and overrides are stored in `effects.json`. In the application, use **Settings > Open effects.json**, save changes, then choose **Reload effects**. New selectable effects become available in the Test Pad and mapping controls. To customize a built-in effect, copy its entry from `effects.default.json` into `effects.json` and keep the same effect ID

Release notes are authored in `CHANGELOG.md`. The build compiles the supported Markdown subset into `changelog.json`; the application then appends customized strength adjustments and backup details from the actual migration report automatically

See Effects Guide in the Settings tab for the recipe format, supported waveform steps, random values, and an example effect

Trigger names, categories, Journal event mappings, and supported Status flag transitions are stored in `triggers.json`. It can be opened and reloaded from Settings. See Trigger Guide in the Settings tab for examples and schema details. A future visual Trigger Manager is planned; special engine actions still require supported action types in the application code

## Telemetry limitations

Elite Dangerous does not journal every physical action. An effect can only trigger automatically when a suitable journal event is available. The Monitoring tab helps identify incoming events and distinguish them from effects dispatched by the application

## Project status

Elite Haptics is an early community release. Effect timing and intensity can vary considerably with the transducer, amplifier, mounting, crossover, and chair or cockpit construction. Feedback and additional effect recipes are welcome

## License

Elite Haptics is available under the [MIT License](LICENSE). You may use, modify, and distribute it for private or commercial purposes, provided the copyright and license notice are retained
