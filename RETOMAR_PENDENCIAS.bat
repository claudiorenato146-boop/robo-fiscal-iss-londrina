@echo off
chcp 65001 >nul
title Robô Fiscal 2.0 - Retomar pendências
cd /d "%~dp0"
set /p COMPETENCIA=Competência no formato MMAAAA (exemplo 072026): 
set "ARQUIVO=pendencias_%COMPETENCIA%.csv"
if not exist "%ARQUIVO%" (
  echo ERRO: arquivo %ARQUIVO% não encontrado nesta pasta.
  pause
  exit /b 1
)
python robo_londrina.py --retomar-pendencias "%ARQUIVO%"
echo.
pause

