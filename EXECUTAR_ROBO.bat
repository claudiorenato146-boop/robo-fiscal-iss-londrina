@echo off
chcp 65001 >nul
title Robô Fiscal 2.0 - Execução completa
cd /d "%~dp0"
python robo_londrina.py
echo.
if errorlevel 1 (
  echo Execução encerrada com pendências ou erro. Leia as mensagens acima.
) else (
  echo Execução concluída.
)
pause

