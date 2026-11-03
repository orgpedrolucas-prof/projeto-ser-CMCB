@echo off
setlocal
cd /d "%~dp0"
title SER - Diagnostico de Inicializacao
echo =========================================
echo SER - DIAGNOSTICO DE INICIALIZACAO
echo =========================================
echo.
where py.exe
py -3.15 --version
echo.
where python.exe
python --version
echo.
if not exist "SER_ERRO_INICIALIZACAO.log" goto sem_log
echo Ultimas linhas do diagnostico:
powershell.exe -NoProfile -Command "Get-Content -LiteralPath 'SER_ERRO_INICIALIZACAO.log' -Tail 15"
goto fim
:sem_log
echo Ainda nao ha registro de diagnostico.
:fim
echo.
pause
