"""Abre o Chrome especial usado pelo Robô Fiscal Londrina."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


URL_ISS = "https://portal.londrina.pr.gov.br/nfse-inicio"


def localizar_chrome() -> Path:
    candidatos = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]
    for candidato in candidatos:
        if candidato.is_file():
            return candidato
    raise FileNotFoundError(
        "Google Chrome não encontrado. Instale o Chrome e tente novamente."
    )


def main() -> None:
    chrome = localizar_chrome()
    perfil = Path(os.environ["LOCALAPPDATA"]) / "RoboFiscalLondrina/ChromeProfile"
    perfil.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            str(chrome),
            "--remote-debugging-port=9222",
            f"--user-data-dir={perfil}",
            URL_ISS,
        ],
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    print("Chrome especial aberto.")
    print("Faça o login manual e deixe o ISS Digital na tela de contribuintes.")


if __name__ == "__main__":
    main()
