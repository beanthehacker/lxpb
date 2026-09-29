@echo off
rem Run by Windows Task Scheduler ("lxpb nightly refresh"); see nightly_refresh.py.
cd /d "%~dp0"
"C:\ProgramData\miniconda3\python.exe" nightly_refresh.py %* >> "data\nightly_refresh.log" 2>&1
