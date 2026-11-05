@echo off
rem Rebuilds GameMod.exe and the folder to share in release\.
cd /d "%~dp0"
.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --name GameMod --distpath . --workpath build_tmp --specpath build_tmp ^
  --icon "%~dp0assets\logo\gamemod.ico" ^
  --add-data "%~dp0assets;assets" ^
  --add-data "%~dp0gamemod\adapters\tlou1\glossary.yaml;gamemod\adapters\tlou1" ^
  --hidden-import keyring.backends.Windows ^
  --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
  --exclude-module numpy --exclude-module PIL --exclude-module pytest ^
  gamemod\main.py
rmdir /s /q build_tmp
.venv\Scripts\python.exe -m gamemod.cli export-pack
.venv\Scripts\python.exe tools\make_release.py
