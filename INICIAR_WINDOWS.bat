@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title SER - Sistema Escolar de Resultados

echo =====================================================
echo       SER - SISTEMA ESCOLAR DE RESULTADOS
echo          INICIALIZACAO CORRIGIDA
echo =====================================================
echo.
echo Esta janela fica aberta para mostrar mensagens de erro.
echo.

if not exist "tools\iniciar_ser_seguro.py" goto pacote_incompleto
if not exist "simr\main.py" goto pacote_incompleto

if not exist ".venv\Scripts\python.exe" goto tentar_py315
".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
if not errorlevel 1 goto usar_venv

:tentar_py315
where py.exe >nul 2>nul
if errorlevel 1 goto tentar_python
py -3.15 -c "import sys" >nul 2>nul
if not errorlevel 1 goto usar_py315
py -c "import sys" >nul 2>nul
if not errorlevel 1 goto usar_py

:tentar_python
where python.exe >nul 2>nul
if errorlevel 1 goto sem_python
python -c "import sys" >nul 2>nul
if not errorlevel 1 goto usar_python
goto sem_python

:usar_venv
".venv\Scripts\python.exe" "tools\iniciar_ser_seguro.py"
goto terminou

:usar_py315
py -3.15 "tools\iniciar_ser_seguro.py"
goto terminou

:usar_py
py "tools\iniciar_ser_seguro.py"
goto terminou

:usar_python
python "tools\iniciar_ser_seguro.py"
goto terminou

:pacote_incompleto
echo ERRO: O pacote SER esta incompleto.
echo Extraia o ZIP inteiro antes de executar o iniciador.
goto falha

:sem_python
echo ERRO: Python nao encontrado pelo Windows.
echo Execute no CMD: py --list
echo Instale Python 3.15.2 x64 ou repare o PATH do Python.
goto falha

:terminou
set "RESULTADO=%ERRORLEVEL%"
if "%RESULTADO%"=="0" goto encerrado
echo.
echo O SER nao iniciou. Consulte SER_ERRO_INICIALIZACAO.log.
echo Caso necessario, envie a ultima mensagem exibida na tela.
goto fim

:falha
set "RESULTADO=1"
goto fim

:encerrado
echo.
echo O servidor SER foi encerrado.

:fim
echo.
echo Pressione qualquer tecla para fechar esta janela.
pause >nul
exit /b %RESULTADO%
