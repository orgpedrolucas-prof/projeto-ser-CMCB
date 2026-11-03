@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title SER - Diagnostico Python 3.15.2

echo ==================================================
echo         SER - DIAGNOSTICO DO PYTHON 3.15.2
echo ==================================================
echo.
echo 1. Launcher py -3.15:
py -3.15 --version 2>&1
if errorlevel 1 echo Python 3.15 nao localizado pelo launcher.
echo.
echo 2. Comando python:
python --version 2>&1
if errorlevel 1 echo Python nao encontrado no PATH.
echo.
echo 3. Versoes registradas:
py --list 2>&1
echo.
echo 4. Ambiente local .venv:
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" --version
  ".venv\Scripts\python.exe" -c "import struct;print('Arquitetura:',struct.calcsize('P')*8,'bits')"
) else (
  echo Ainda nao existe ambiente local .venv.
)
echo.
echo Se tiver Python 3.15.2 instalado e nao reconhecido,
echo abra https://www.python.org/downloads/release/python-3152/
echo e repare a instalacao usando Add Python to PATH / launcher.
echo.
pause
