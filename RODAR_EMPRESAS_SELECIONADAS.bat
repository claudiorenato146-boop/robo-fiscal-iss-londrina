@echo off
chcp 65001 >nul
title Robô Fiscal 2.0 - Rodar empresas selecionadas
cd /d "%~dp0"
echo.
echo Digite os codigos das empresas separados por PONTO.
echo Exemplo:  545.582.548.547
echo.
set /p CODIGOS=Codigos: 
python robo_londrina.py --codigos "%CODIGOS%"
echo.
if errorlevel 1 (
  echo Execucao encerrada com pendencias ou erro. Leia as mensagens acima.
) else (
  echo Execucao concluida.
)
pause
