@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title SER - Acesso Wi-Fi (TESTE)
echo ATENCAO: a rede Wi-Fi usa HTTP sem criptografia.
echo Use apenas dados ficticios durante os testes.
echo.
if not exist ".venv\Scripts\python.exe" (
  echo Primeiro execute INICIAR_WINDOWS.bat para configurar o sistema.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" ser_web.py --wifi
if errorlevel 1 pause
