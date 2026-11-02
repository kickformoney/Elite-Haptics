@echo off
setlocal

if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv || exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r requirements-build.txt || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm elite-transducers-ui.spec || exit /b 1

copy /Y "effects.json" "dist\effects.json" >nul
copy /Y "triggers.json" "dist\triggers.json" >nul
copy /Y "EFFECTS_GUIDE.md" "dist\EFFECTS_GUIDE.md" >nul
copy /Y "TRIGGERS_GUIDE.md" "dist\TRIGGERS_GUIDE.md" >nul

echo.
echo Build complete: dist\elite-haptics.exe
