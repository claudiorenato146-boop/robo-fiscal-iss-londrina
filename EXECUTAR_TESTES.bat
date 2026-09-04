@echo off
chcp 65001 >nul
title Robô Fiscal 2.0 - Testes internos
cd /d "%~dp0"
python -m unittest -v test_robo_londrina.py
echo.
pause

