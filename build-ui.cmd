@echo off
setlocal

if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv || exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements-build.txt || exit /b 1
".venv\Scripts\python.exe" compile_changelog.py CHANGELOG.md changelog.json --github-output build\release-notes.md || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm elite-haptics-ui.spec || exit /b 1

copy /Y "effects.default.json" "dist\effects.default.json" >nul
copy /Y "effects.legacy-manifest.json" "dist\effects.legacy-manifest.json" >nul
copy /Y "effect_migrations.json" "dist\effect_migrations.json" >nul
copy /Y "CHANGELOG.md" "dist\CHANGELOG.md" >nul
copy /Y "changelog.json" "dist\changelog.json" >nul
copy /Y "triggers.json" "dist\triggers.json" >nul
copy /Y "EFFECTS_GUIDE.md" "dist\EFFECTS_GUIDE.md" >nul
copy /Y "TRIGGERS_GUIDE.md" "dist\TRIGGERS_GUIDE.md" >nul

echo.
echo Build complete: dist\elite-haptics.exe
