@echo off
chcp 65001 >nul
title Robô Fiscal 2.0 - Conferência dos arquivos
cd /d "%~dp0"
set /p COMPETENCIA=Competência no formato MMAAAA (exemplo 072026): 
python verificar_pendencias.py "%COMPETENCIA%"
echo.
pause

