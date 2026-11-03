@echo off
cd /d "%~dp0"
title SER - Backup
if not exist ".venv\Scripts\python.exe" (
 echo Abra o SER pelo menos uma vez antes de gerar backup.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" tools\backup_ser.py
pause
