@echo off
chcp 65001 >nul
title Instalação - Robô Fiscal 2.0
cd /d "%~dp0"

echo Instalando os componentes necessários...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERRO: a instalação não foi concluída.
  pause
  exit /b 1
)

echo.
echo Instalação concluída com sucesso.
pause

