"""
config.py

Configuração do Robô ISS Digital Londrina.

A partir de 29/07/2026: o robô NÃO faz mais login sozinho — você loga
manualmente (certificado + captcha) num Chrome aberto com depuração
remota, e o robô só se conecta a essa sessão já autenticada (ver
README). Por isso, o caminho/senha do certificado agora são OPCIONAIS
aqui — só usados para o aviso de vencimento, não para autenticação real.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
try:
    from dotenv import load_dotenv
except ImportError:  # permite executar testes antes da instalação completa
    def load_dotenv() -> bool:
        return False

load_dotenv()


@dataclass(frozen=True)
class Config:
    # OPCIONAL — só para o aviso de vencimento do certificado (não é mais
    # usado para autenticar; o login é manual, no seu próprio Chrome)
    caminho_certificado_escritorio: str = os.getenv("CAMINHO_CERTIFICADO_ESCRITORIO", "")
    senha_certificado_escritorio: str = os.getenv("SENHA_CERTIFICADO_ESCRITORIO", "")

    # Lista de clientes de Londrina. Colunas obrigatórias:
    # codigo_cliente, cnpj, apelido, pasta_dominio. A coluna opcional
    # razao_social é usada para exibir o nome completo no controle.
    caminho_csv_clientes_londrina: str = os.getenv(
        "CAMINHO_CSV_CLIENTES_LONDRINA", "clientes_londrina.csv"
    )

    # Pasta raiz de importação do Domínio (NFS-e EMITIDAS) — dentro dela já
    # devem existir as pastas de cada cliente, no formato "{codigo}-{apelido}"
    caminho_raiz_importacao_dominio: str = os.getenv(
        "CAMINHO_RAIZ_IMPORTACAO_DOMINIO", ""
    )

    # Pasta raiz de importação do Domínio para NFS-e RECEBIDAS/TOMADAS —
    # adicionada em 03/08/2026: emitidas e recebidas passaram a ir para
    # pastas raiz DIFERENTES (antes ambas iam para a mesma). Mesmo formato
    # de pasta de cliente ("{codigo}-{apelido}"), mesma regra de nunca
    # criar pasta de cliente sozinho.
    caminho_raiz_importacao_tomados: str = os.getenv(
        "CAMINHO_RAIZ_IMPORTACAO_TOMADOS", ""
    )

    # Pasta raiz para os relatórios Excel de conferência e para a planilha
    # única PLANILHA RELATORIO MENSAL.xlsx. A planilha recebe uma aba por
    # competência. Nas subpastas de relatório, o robô pode criar a pasta
    # do cliente se ela ainda não existir.
    caminho_raiz_relatorios_conferencia: str = os.getenv(
        "CAMINHO_RAIZ_RELATORIOS_CONFERENCIA", ""
    )

    # Porta de depuração remota do Chrome que o robô vai tentar conectar
    porta_debug_chrome: int = int(os.getenv("PORTA_DEBUG_CHROME", "9222"))


config = Config()

DIAS_AVISO_VENCIMENTO_CERTIFICADO = 15


def _verificar_validade_certificado() -> str | None:
    """
    Abre o certificado (se o caminho foi informado) e confere a validade
    — só para aviso, já que a autenticação real agora é manual. Retorna
    None se o caminho não foi informado, se não conseguir ler, ou se
    estiver tudo bem.
    """
    if not config.caminho_certificado_escritorio or not config.senha_certificado_escritorio:
        return None

    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
    except ImportError:
        return None

    try:
        dados = open(config.caminho_certificado_escritorio, "rb").read()
        _, certificado, _ = pkcs12.load_key_and_certificates(
            dados, config.senha_certificado_escritorio.encode("utf-8")
        )
    except Exception:
        return None

    if certificado is None:
        return None

    validade_final = certificado.not_valid_after_utc
    dias_restantes = (validade_final - datetime.now(timezone.utc)).days

    if dias_restantes < 0:
        return f"Certificado VENCIDO em {validade_final:%d/%m/%Y} — renove assim que possível."
    if dias_restantes <= DIAS_AVISO_VENCIMENTO_CERTIFICADO:
        return (
            f"Certificado vence em {dias_restantes} dia(s) "
            f"({validade_final:%d/%m/%Y %H:%M}) — providencie a renovação em breve."
        )
    return None


def validar_config() -> list[str]:
    """Problemas que IMPEDEM a execução."""
    problemas = []

    if not config.caminho_raiz_importacao_dominio:
        problemas.append("CAMINHO_RAIZ_IMPORTACAO_DOMINIO não preenchida no .env")
    elif not os.path.isdir(config.caminho_raiz_importacao_dominio):
        problemas.append(
            f"CAMINHO_RAIZ_IMPORTACAO_DOMINIO não é uma pasta válida: "
            f"{config.caminho_raiz_importacao_dominio}"
        )

    if not config.caminho_raiz_importacao_tomados:
        problemas.append("CAMINHO_RAIZ_IMPORTACAO_TOMADOS não preenchida no .env")
    elif not os.path.isdir(config.caminho_raiz_importacao_tomados):
        problemas.append(
            f"CAMINHO_RAIZ_IMPORTACAO_TOMADOS não é uma pasta válida: "
            f"{config.caminho_raiz_importacao_tomados}"
        )

    if not config.caminho_raiz_relatorios_conferencia:
        problemas.append("CAMINHO_RAIZ_RELATORIOS_CONFERENCIA não preenchida no .env")
    elif not os.path.isdir(config.caminho_raiz_relatorios_conferencia):
        problemas.append(
            f"CAMINHO_RAIZ_RELATORIOS_CONFERENCIA não é uma pasta válida: "
            f"{config.caminho_raiz_relatorios_conferencia}"
        )
    if not os.path.exists(config.caminho_csv_clientes_londrina):
        problemas.append(
            f"Lista de clientes de Londrina não encontrada em: "
            f"{config.caminho_csv_clientes_londrina}"
        )

    return problemas


def obter_avisos_config() -> list[str]:
    """Avisos que NÃO impedem a execução (ex.: certificado perto de vencer)."""
    avisos = []
    aviso_certificado = _verificar_validade_certificado()
    if aviso_certificado:
        avisos.append(aviso_certificado)
    return avisos
