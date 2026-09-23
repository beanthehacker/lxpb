@echo off
cd /d "%~dp0"
git pull origin main
start "" pythonw ss_m5_confl2\regen_app.py
