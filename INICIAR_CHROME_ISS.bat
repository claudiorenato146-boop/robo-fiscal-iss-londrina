@echo off
chcp 65001 >nul
title Chrome especial - ISS Digital Londrina

set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
  echo ERRO: Google Chrome não encontrado neste computador.
  pause
  exit /b 1
)

set "PERFIL=%LOCALAPPDATA%\RoboFiscalLondrina\ChromeProfile"
if not exist "%PERFIL%" mkdir "%PERFIL%"
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%PERFIL%" "https://portal.londrina.pr.gov.br/nfse-inicio"

echo.
echo Faça o login manual com certificado e captcha.
echo Depois deixe o ISS Digital na tela inicial de contribuintes.
echo Só então execute EXECUTAR_ROBO.bat.
pause

