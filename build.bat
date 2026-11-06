@echo off
rem Rebuilds GameMod.exe, the subtitle packs and the folder to share in release\.
cd /d "%~dp0"
.venv\Scripts\python.exe tools\build_exe.py || exit /b 1
.venv\Scripts\python.exe -m gamemod.cli export-pack || exit /b 1
.venv\Scripts\python.exe -m gamemod.cli --game re2 export-pack || exit /b 1
.venv\Scripts\python.exe -m gamemod.cli --game msmr export-pack || exit /b 1
.venv\Scripts\python.exe tools\make_release.py
