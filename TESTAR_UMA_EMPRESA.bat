@echo off
chcp 65001 >nul
title Robô Fiscal 2.0 - Teste de uma empresa
cd /d "%~dp0"
set /p CODIGO=Digite o código da empresa: 
python robo_londrina.py --codigo-cliente "%CODIGO%"
echo.
pause

