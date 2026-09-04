@echo off
chcp 65001 >nul
title Robô Fiscal 2.0 - Teste geral (diagnostico, sem baixar XML)
cd /d "%~dp0"
echo.
echo ====================================================================
echo  TESTE GERAL / DIAGNOSTICO
echo.
echo  Passa por TODAS as empresas da planilha so para descobrir o perfil
echo  de cada uma (se seleciona, se abre as abas, se falta procuracao).
echo  NAO baixa nenhum XML e NAO altera a planilha de controle.
echo  Ao final gera um relatorio TXT nesta pasta (teste_geral_...txt).
echo ====================================================================
echo.
echo  Confirme que o Chrome ja esta aberto e LOGADO no ISS Digital.
echo.
pause
python robo_londrina.py --teste-geral
echo.
echo Relatorio TXT gerado nesta pasta. Abra para ver o perfil de cada empresa.
pause
