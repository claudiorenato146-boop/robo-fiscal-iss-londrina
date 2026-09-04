"""
robo_londrina.py

Robô do Portal ISS Digital de Londrina (app.londrina.pr.gov.br). Login
ÚNICO com o certificado do escritório (via GovBR), captcha resolvido
manualmente UMA VEZ, e depois processa cada cliente de Londrina: seleciona
o contribuinte, baixa NFS-e Emitidas e Recebidas (ZIP), e salva na pasta
de importação automática do Domínio — SEM extrair o ZIP.

═══════════════════════════════════════════════════════════════════════
LEIA ANTES DE RODAR — O QUE ESTÁ CONFIRMADO E O QUE AINDA É SUPOSIÇÃO
═══════════════════════════════════════════════════════════════════════

CONFIRMADO (por HTML/print reais, 28/07/2026):
  - Botão de login por certificado: id="login-certificate", redireciona
    para certificado.sso.acesso.gov.br (domínio do GovBR, não da
    prefeitura — certificado precisa estar registrado para esse domínio)
  - Captcha aparece UMA VEZ no login (não por nota, diferente do Portal
    Nacional)
  - Campo Contribuinte: combobox com data-value contendo o CNPJ puro;
    NÃO é possível pesquisar por CNPJ — só rolar a lista visualmente
  - Links "NFs-e Emitidas" e "NFs-e Recebidas" têm parâmetros de sessão
    que mudam a cada login — precisam ser clicados de verdade, nunca
    navegados por URL fixa
  - Campos de data: #P27_DATA_INICIO_input / #P27_DATA_FIM_input
    (tela Emitidas) e #P29_DATA_INICIO_input / #P29_DATA_FIM_input
    (tela Recebidas) — somente leitura, exigem calendário
  - Calendário sempre abre no mês REAL atual, nunca memoriza navegação
    anterior; botão "Próximo Mês" tem aria-label confirmado
  - Botão "Atualizar" (recarregar lista): aria-label="Atualizar"
  - Fluxo de download é DE DOIS CLIQUES: botão "XMLS" abre confirmação,
    depois um segundo botão "XMLS" (class="js-confirmBtn") efetiva o
    download
  - Limite de 60 dias por consulta (diferente do Portal Nacional, que é 30)
  - Nome dos ZIPs: xml_nfse_emitidas_{cnpj}.zip / xml_nfse_recebidas_{cnpj}.zip

NÃO CONFIRMADO / INFERIDO (marcado como TODO no código):
  - Botão "Mês Anterior": inferido por simetria com "Próximo Mês"
    (aria-label="Mês Anterior"), com fallback para o ícone (icon-prev)
  - Seleção de dia no calendário quando o número do dia aparece
    duplicado (dias "esmaecidos" do mês anterior/seguinte no mesmo
    calendário) — usei uma heurística (dias baixos = primeira
    ocorrência, dias altos = última ocorrência), não testada ao vivo
  - Se definir o CNPJ direto via data-value (sem abrir a lista visual)
    funciona, ou se é sempre necessário abrir e clicar na lista

Ou seja: a parte mais provável de precisar ajuste no primeiro teste real
é a seleção de dia no calendário quando há ambiguidade.

Requisitos:
    pip install playwright python-dotenv cryptography
    playwright install chromium
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import re
import shutil
import sys
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout
except ImportError:  # permite rodar os testes de arquivos antes da instalação completa
    sync_playwright = None
    Page = BrowserContext = Any
    PWTimeout = TimeoutError

from config import config, validar_config, obter_avisos_config
from controle_resultados import atualizar_controle, inicializar_controle

logger = logging.getLogger("robo_londrina")

# ═══════════════════════════════════════════════════════════════════════
# CONSTANTES
# ═══════════════════════════════════════════════════════════════════════

# URL_PARA_LOGIN_MANUAL: não é mais navegada pelo robô — é só a referência
# de onde VOCÊ deve ir manualmente antes de rodar o script (ver README).
URL_PARA_LOGIN_MANUAL = "https://portal.londrina.pr.gov.br/nfse-inicio"
CAMINHO_PAGINA_INICIAL_ISS = "/app/r/fazenda_front/app-front-iss/principal"
LIMITE_DIAS_POR_CONSULTA = 60  # confirmado neste portal (Nacional era 30)
PADRAO_CODIGO_PASTA = re.compile(r"^\s*(\d+)\s*-\s*(.+)$")  # aceita "1-EMPRESA" e "1 - EMPRESA"

TIMEOUT_ESPERA_CAPTCHA_LOGIN_SEGUNDOS = 300  # 5 min — etapa manual maior agora (clique + captcha)
PAUSA_ENTRE_CLIENTES_SEGUNDOS = 12  # aumentado de 10 → 12 (05/08/2026): dá mais fôlego entre empresas

# Limites máximos para o ISS Digital em dias de lentidão. Quando a tela
# responde rápido, o robô continua antes do limite.
TIMEOUT_ELEMENTO_MS = 120_000
TIMEOUT_NAVEGACAO_MS = 120_000
TIMEOUT_DOWNLOAD_MS = 300_000
PAUSA_ESTABILIZACAO_MS = 1_500
TENTATIVAS_PORTAL_LENTO = 4

# ─── Troca de empresa: o ponto mais frágil (ajustado 05/08/2026) ────────
# Diagnóstico real: ao trocar de empresa, o robô setava o CNPJ mas o NOME
# do contribuinte demorava a aparecer na tela; ele seguia antes da hora e
# dava erro. Agora, depois de setar o CNPJ, o robô ESPERA o nome renderizar
# (confere o próprio texto do campo) e re-tenta a seleção inteira algumas
# vezes em dias de lentidão, em vez de desistir na primeira.
TIMEOUT_NOME_CONTRIBUINTE_MS = 45_000   # espera dedicada até o nome do contribuinte aparecer no campo
TENTATIVAS_SELECAO_CONTRIBUINTE = 3     # re-tenta a seleção inteira do contribuinte
PAUSA_APOS_SETVALUE_MS = 2_500          # respiro após setar o CNPJ, antes de conferir o nome
PAUSA_ANTES_RETENTAR_SELECAO_MS = 4_000 # base da pausa crescente entre re-tentativas de seleção

# Frases que o portal mostra quando o período consultado NÃO tem nota
# (sem movimento) — usadas para marcar "X" em vez de "ERRO". A tela de
# Apurações (inicial) usa "Sem NFS-e na competência"; as telas de
# Emitidas/Recebidas usam "Nenhum registro encontrado" e variações.
FRASES_SEM_MOVIMENTO = [
    "Nenhum registro encontrado",
    "Nenhum dado encontrado",
    "sem registros",
    "Sem NFS-e na competência",
    "Sem NFS-e",
    "Não há registros",
    "Nenhuma nota",
]

# Sinais de que a sessão do gov.br caiu (deslogou no meio da rodada). Quando
# isso acontece, TODA empresa seguinte cai na tela de login e gasta minutos
# esperando elementos que nunca vão aparecer — por isso o robô agora PARA na
# hora, em vez de falhar 300 empresas em cascata.
TRECHOS_URL_DESLOGADO = ["acesso.gov.br", "sso.acesso.gov.br", "certificado.sso", "/login"]
FRASES_DESLOGADO = ["Identifique-se no gov.br", "Digite seu CPF", "Uma conta gov.br"]

MESES_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def aguardar_portal(page: Page, etapa: str, timeout_ms: int = TIMEOUT_ELEMENTO_MS) -> None:
    """Espera o processamento AJAX do Oracle APEX terminar."""
    # Permite que o indicador de carregamento apareça antes da conferência.
    page.wait_for_timeout(750)
    indicadores = page.locator(".u-Processing")
    inicio = time.monotonic()
    for indice in range(indicadores.count()):
        restante = timeout_ms - int((time.monotonic() - inicio) * 1000)
        if restante <= 0:
            raise RuntimeError(f"Portal demorou demais durante: {etapa}")
        try:
            indicadores.nth(indice).wait_for(state="hidden", timeout=restante)
        except PWTimeout as erro:
            raise RuntimeError(
                f"Portal não terminou o processamento em {timeout_ms // 1000}s "
                f"durante: {etapa}"
            ) from erro
    # Evita clicar no passo seguinte enquanto a tela ainda está renderizando.
    page.wait_for_timeout(PAUSA_ESTABILIZACAO_MS)


def detectar_sessao_expirada(page: Page) -> bool:
    """
    Detecta se a sessão do gov.br caiu (o robô foi deslogado no meio da
    rodada). Adicionado em 05/08/2026 após prints reais mostrarem a tela
    de login do gov.br em dezenas de "falhas" — na verdade era UMA queda de
    sessão que derrubou todo o resto em cascata.

    Confere dois sinais, sem nunca levantar exceção (só retorna True/False):
      1) a URL saiu do portal de Londrina e foi para um domínio de login;
      2) a página mostra algum texto típico da tela de identificação gov.br.
    """
    try:
        url = (page.url or "").lower()
    except Exception:
        return False

    # Se ainda está no portal de Londrina, considera a sessão viva.
    if "app.londrina.pr.gov.br" in url:
        return False
    if any(trecho in url for trecho in TRECHOS_URL_DESLOGADO):
        return True

    # Fora do portal de Londrina: confere o conteúdo por segurança.
    for frase in FRASES_DESLOGADO:
        try:
            if page.get_by_text(frase, exact=False).count() > 0:
                return True
        except Exception:
            pass
    return False


def _texto_contribuinte_selecionado(page: Page) -> str:
    """
    Lê o que está escrito no campo Contribuinte (ex.: '99.900/0001-50 -
    Padaria do Exemplo...'). É esse texto que demora a aparecer quando
    se troca de empresa — o CNPJ é setado por baixo, mas o NOME só renderiza
    um instante depois. Nunca levanta exceção.
    """
    campo = page.locator('input[aria-labelledby="P5_CONTRIBUINTE_LABEL"]')
    try:
        valor = campo.input_value() or ""
    except Exception:
        valor = ""
    if not valor:
        try:
            valor = campo.get_attribute("data-value") or ""
        except Exception:
            valor = ""
    return valor


def aguardar_nome_contribuinte(
    page: Page, cliente: "ClienteLondrina", timeout_ms: int = TIMEOUT_NOME_CONTRIBUINTE_MS
) -> bool:
    """
    Espera, de verdade, o NOME/CNPJ do contribuinte alvo aparecer no campo
    depois da troca de empresa — em vez de seguir cego e falhar. Retorna
    True assim que o texto do campo contiver o CNPJ da empresa; False se
    estourar o tempo.
    """
    fim = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < fim:
        digitos = re.sub(r"\D", "", _texto_contribuinte_selecionado(page))
        if cliente.cnpj in digitos:
            return True
        page.wait_for_timeout(500)
    return False


def pagina_indica_sem_movimento(page: Page) -> bool:
    """
    True quando a tela mostra explicitamente que NÃO há nota no período
    (sem movimento). Serve para marcar 'X' em vez de 'ERRO' quando o botão
    de download não aparece simplesmente porque não há o que baixar.
    """
    for frase in FRASES_SEM_MOVIMENTO:
        try:
            if page.get_by_text(frase, exact=False).count() > 0:
                return True
        except Exception:
            pass
    return False


class SessaoExpiradaError(Exception):
    """
    Levantada quando o gov.br desloga no meio da rodada (a tela volta para o
    login). Como o login é MANUAL, o robô não tem como se reautenticar
    sozinho — então isto é FATAL para a execução inteira: em vez de deixar
    cada empresa seguinte gastar minutos esperando um elemento que nunca vai
    aparecer, o robô para na hora e avisa para relogar.
    """


# ═══════════════════════════════════════════════════════════════════════
# MODELOS DE DADOS
# ═══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ClienteLondrina:
    codigo: str
    cnpj: str
    apelido: str
    pasta_dominio: str
    razao_social: str = ""

    @property
    def nome_exibicao(self) -> str:
        return self.razao_social or self.apelido or self.codigo


@dataclass(frozen=True)
class PastaDominio:
    codigo: str
    nome_completo_pasta: str
    caminho: Path


@dataclass(frozen=True)
class ResultadoCliente:
    codigo_cliente: str
    nome: str
    status: str  # "processado" | "pendencia_sem_pasta" | "falha" 
    detalhe: str | None = None
    cnpj: str = ""
    emitidos: str = ""
    recebidos: str = ""
    relatorio: str = ""
    xml_emitidos: int | None = None
    xml_recebidos: int | None = None


# ═══════════════════════════════════════════════════════════════════════
# ENTRADA DE PERÍODO (reaproveitando o mesmo padrão já validado)
# ═══════════════════════════════════════════════════════════════════════

def solicitar_periodo_ao_usuario() -> tuple[date, date, str]:
    print("\n" + "=" * 60)
    print("PERÍODO A CONSULTAR")
    print("=" * 60)
    print("Escolha uma opção:")
    print("  1) Informar a competência do mês (ex.: 07.2026)")
    print("  2) Informar um intervalo de datas manualmente (DD/MM/AAAA)")
    escolha = input("Digite 1 ou 2: ").strip()

    if escolha == "1":
        competencia = input("Competência (MM.AAAA, ex.: 07.2026): ").strip()
        try:
            mes, ano = competencia.split(".")
            mes, ano = int(mes), int(ano)
            data_inicial = date(ano, mes, 1)
        except (ValueError, IndexError):
            print(f"\nErro: '{competencia}' não está no formato MM.AAAA (ex.: 07.2026). Encerrando.")
            sys.exit(1)
        if mes == 12:
            data_final = date(ano, 12, 31)
        else:
            data_final = date(ano, mes + 1, 1) - timedelta(days=1)
        data_inicial, data_final = limitar_periodo_ate_hoje(
            data_inicial, data_final
        )
        # Rótulo no formato MMAAAA (sem separador), confirmado como o
        # padrão exigido pela pasta de importação do Domínio
        rotulo_mmaaaa = f"{mes:02d}{ano}"
        return data_inicial, data_final, rotulo_mmaaaa

    elif escolha == "2":
        bruta_inicial = input("Data inicial (DD/MM/AAAA): ").strip()
        bruta_final = input("Data final (DD/MM/AAAA): ").strip()
        try:
            data_inicial = datetime.strptime(bruta_inicial, "%d/%m/%Y").date()
            data_final = datetime.strptime(bruta_final, "%d/%m/%Y").date()
        except ValueError:
            print("\nErro: data em formato inválido. Use DD/MM/AAAA. Encerrando.")
            sys.exit(1)

        # Validação adicionada após auditoria externa (28/07/2026):
        # data invertida antes resultava em "0 blocos" tratado como
        # sucesso sem notas, silenciosamente — agora é erro explícito.
        if data_inicial > data_final:
            print(
                f"\nErro: data inicial ({bruta_inicial}) é posterior à data "
                f"final ({bruta_final}). Encerrando."
            )
            sys.exit(1)

        # Validação adicionada após auditoria externa: um intervalo
        # manual cruzando mês salvaria TUDO na competência do mês
        # inicial, misturando notas de meses diferentes na mesma pasta.
        # Como o destino é organizado por competência (uma pasta por
        # mês), é mais seguro recusar do que salvar errado.
        if (data_inicial.year, data_inicial.month) != (data_final.year, data_final.month):
            print(
                f"\nErro: o intervalo informado ({bruta_inicial} a {bruta_final}) "
                f"cruza mais de um mês. Isso misturaria notas de meses "
                f"diferentes na mesma pasta de competência. Rode o robô uma "
                f"vez para cada mês separadamente."
            )
            sys.exit(1)

        data_inicial, data_final = limitar_periodo_ate_hoje(
            data_inicial, data_final
        )
        rotulo_mmaaaa = f"{data_inicial.month:02d}{data_inicial.year}"
        return data_inicial, data_final, rotulo_mmaaaa

    else:
        print("Opção inválida. Encerrando.")
        sys.exit(1)


def limitar_periodo_ate_hoje(
    data_inicial: date, data_final: date, hoje: date | None = None
) -> tuple[date, date]:
    """Impede que o calendário tente clicar em datas futuras desabilitadas."""
    hoje = hoje or date.today()
    if data_inicial > hoje:
        print(
            f"\nErro: o período começa em {data_inicial:%d/%m/%Y}, que é "
            f"posterior à data de hoje ({hoje:%d/%m/%Y}). Encerrando."
        )
        raise SystemExit(1)
    if data_final > hoje:
        print(
            f"\nAviso: a competência ainda não terminou. A data final foi "
            f"ajustada de {data_final:%d/%m/%Y} para hoje "
            f"({hoje:%d/%m/%Y})."
        )
        data_final = hoje
    return data_inicial, data_final


def dividir_periodo_em_blocos(inicio: date, fim: date) -> list[tuple[date, date]]:
    """Respeita o limite de 60 dias por consulta deste portal."""
    blocos = []
    inicio_bloco = inicio
    while inicio_bloco <= fim:
        fim_bloco = min(inicio_bloco + timedelta(days=LIMITE_DIAS_POR_CONSULTA - 1), fim)
        blocos.append((inicio_bloco, fim_bloco))
        inicio_bloco = fim_bloco + timedelta(days=1)
    return blocos


# ═══════════════════════════════════════════════════════════════════════
# CLIENTES
# ═══════════════════════════════════════════════════════════════════════

def carregar_clientes_londrina(caminho_csv: str) -> list[ClienteLondrina]:
    """
    Formato esperado: codigo_cliente,cnpj,apelido,pasta_dominio
    (o mesmo clientes.csv geral serve, desde que tenha essas colunas —
    ou uma lista filtrada só com os clientes de Londrina, como o usuário
    vai fornecer).
    """
    caminho = Path(caminho_csv)
    if not caminho.exists():
        raise FileNotFoundError(f"Lista de clientes não encontrada: {caminho}")

    clientes = []
    # ``utf-8-sig`` aceita tanto CSV UTF-8 comum quanto CSV com BOM
    # (marca invisível que o Excel costuma gravar no início do arquivo).
    with open(caminho, newline="", encoding="utf-8-sig") as arquivo:
        leitor = csv.DictReader(arquivo)
        colunas_necessarias = {"codigo_cliente", "cnpj", "apelido", "pasta_dominio"}
        if not colunas_necessarias.issubset(set(leitor.fieldnames or [])):
            raise ValueError(
                f"CSV precisa ter as colunas {colunas_necessarias}. "
                f"Colunas encontradas: {leitor.fieldnames}"
            )
        for linha in leitor:
            clientes.append(
                ClienteLondrina(
                    codigo=linha["codigo_cliente"].strip(),
                    cnpj="".join(c for c in linha["cnpj"] if c.isdigit()),
                    apelido=linha["apelido"].strip(),
                    pasta_dominio=linha["pasta_dominio"].strip(),
                    razao_social=(linha.get("razao_social") or "").strip(),
                )
            )
    logger.info("Carregados %d clientes de Londrina.", len(clientes))
    return clientes


def formatar_cnpj(cnpj_digitos: str) -> str:
    """'99900001000150' -> '99.900.001/0001-50' — formato usado na lista visual."""
    c = cnpj_digitos
    return f"{c[0:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}"


def cnpj_valido(cnpj_digitos: str) -> bool:
    """
    Valida o CNPJ pelo algoritmo oficial de dígito verificador (módulo
    11) — não apenas a quantidade de dígitos. Adicionado após auditoria
    externa (28/07/2026) ter encontrado 3 CNPJs com dígito verificador
    inválido no clientes_londrina.csv, que avançariam até a busca no
    portal e falhariam lá sem explicação clara.
    """
    c = cnpj_digitos
    if len(c) != 14 or c == c[0] * 14:
        return False

    def _calcular_digito(base: str, pesos: list[int]) -> int:
        soma = sum(int(d) * p for d, p in zip(base, pesos))
        resto = soma % 11
        return 0 if resto < 2 else 11 - resto

    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    digito1 = _calcular_digito(c[:12], pesos1)
    digito2 = _calcular_digito(c[:12] + str(digito1), pesos2)

    return c[12:14] == f"{digito1}{digito2}"


# ═══════════════════════════════════════════════════════════════════════
# PASTA DE IMPORTAÇÃO DO DOMÍNIO — NUNCA CRIA PASTA DE CLIENTE
# ═══════════════════════════════════════════════════════════════════════

def _normalizar_nome_pasta(nome: str) -> str:
    """Normaliza um nome de pasta apenas para comparação segura."""
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", nome)
        if unicodedata.category(caractere) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "", sem_acentos.casefold())


def descobrir_pastas_de_cliente_dominio(
    raiz: Path, clientes_por_codigo: dict[str, ClienteLondrina] | None = None
) -> dict[str, PastaDominio]:
    """
    Varre a pasta raiz de importação e mapeia código -> pasta, usando o
    padrão real confirmado "{codigo}-{apelido}" (sem espaço). Nunca
    reconstrói nome — sempre lê o que já existe.

    Quando o mesmo código aparece em mais de uma pasta, tenta resolver
    usando o nome exato cadastrado na coluna ``pasta_dominio`` do CSV.
    Isso permite manter uma pasta antiga e uma atual sem o robô escolher
    no escuro. Se o CSV não permitir identificar uma única pasta, o código
    continua excluído e vira pendência explícita.
    """
    candidatos: dict[str, list[PastaDominio]] = {}

    if not raiz.exists():
        raise FileNotFoundError(f"Pasta raiz de importação não encontrada: {raiz}")
    if not raiz.is_dir():
        raise NotADirectoryError(f"Caminho de importação não é uma pasta: {raiz}")

    for item in raiz.iterdir():
        if not item.is_dir():
            continue
        m = PADRAO_CODIGO_PASTA.match(item.name)
        if not m:
            continue
        codigo = m.group(1)
        candidatos.setdefault(codigo, []).append(
            PastaDominio(codigo=codigo, nome_completo_pasta=item.name, caminho=item)
        )

    mapa: dict[str, PastaDominio] = {}
    codigos_duplicados: set[str] = set()
    for codigo, opcoes in candidatos.items():
        if len(opcoes) == 1:
            mapa[codigo] = opcoes[0]
            continue

        cliente = (clientes_por_codigo or {}).get(codigo)
        esperado = (cliente.pasta_dominio or "").strip() if cliente else ""
        correspondentes = [
            opcao for opcao in opcoes
            if esperado and _normalizar_nome_pasta(opcao.nome_completo_pasta) == _normalizar_nome_pasta(esperado)
        ]
        if len(correspondentes) == 1:
            mapa[codigo] = correspondentes[0]
            logger.warning(
                "Código %s possui %d pastas; usada '%s' conforme pasta_dominio do CSV.",
                codigo, len(opcoes), correspondentes[0].nome_completo_pasta,
            )
            continue

        nomes = "', '".join(opcao.nome_completo_pasta for opcao in opcoes)
        logger.error(
            "Código %s duplicado em mais de uma pasta ('%s') — REMOVIDO do "
            "processamento. O CSV não indicou uma única pasta_dominio correspondente.",
            codigo, nomes,
        )
        codigos_duplicados.add(codigo)

    logger.info(
        "Descobertas %d pastas de cliente válidas em %s (%d código(s) "
        "duplicado(s) excluído(s))",
        len(mapa), raiz, len(codigos_duplicados),
    )
    return mapa


def obter_pasta_competencia(
    cliente: ClienteLondrina, pastas_clientes: dict[str, PastaDominio], competencia_mmaaaa: str
) -> Path | None:
    """
    Regra confirmada pelo usuário: a pasta do CLIENTE nunca é criada pelo
    robô — todas já devem existir. Se não existir, é pendência, não erro
    silencioso nem criação automática.

    A subpasta de COMPETÊNCIA (ex.: "062026"), por outro lado, pode ser
    criada — é rotina mensal esperada. Essa distinção é uma inferência
    (não houve proibição explícita para a subpasta de competência),
    sinalizada aqui para revisão se necessário.
    """
    if cliente.codigo not in pastas_clientes:
        return None  # pendência — pasta do cliente não existe

    pasta_cliente = pastas_clientes[cliente.codigo].caminho
    # A pasta só será criada depois que um ZIP válido for realmente
    # baixado. Antes, uma falha de seleção ou de calendário deixava centenas
    # de competências vazias, dando a impressão de processamento concluído.
    return pasta_cliente / competencia_mmaaaa


def obter_pasta_competencia_relatorio(
    cliente: ClienteLondrina, pastas_clientes: dict[str, PastaDominio],
    raiz_relatorios: Path, competencia_mmaaaa: str,
) -> Path:
    """
    REGRA DIFERENTE das pastas de importação (achado explícito do
    usuário, 03/08/2026): para os relatórios Excel de conferência, o
    robô PODE criar a pasta do cliente se ela não existir — não é a
    importação fiscal real, é só material de conferência.

    Se já existe uma pasta desse cliente na raiz de relatórios, reusa.
    Senão, cria no padrão "{codigo}-{apelido}" (mesma convenção usada
    nas pastas de importação, por consistência) — ou só "{codigo}" se o
    cliente não tiver apelido conhecido.
    """
    if cliente.codigo in pastas_clientes:
        pasta_cliente = pastas_clientes[cliente.codigo].caminho
    else:
        nome_pasta = f"{cliente.codigo}-{cliente.apelido}" if cliente.apelido else cliente.codigo
        pasta_cliente = raiz_relatorios / nome_pasta
        pasta_cliente.mkdir(parents=True, exist_ok=True)
        logger.info("Pasta de relatório criada para %s: %s", cliente.apelido or cliente.codigo, pasta_cliente)

    return pasta_cliente / competencia_mmaaaa


# ═══════════════════════════════════════════════════════════════════════
# LOGIN POR CERTIFICADO (GovBR) — ÚNICO, UMA VEZ POR EXECUÇÃO
# ═══════════════════════════════════════════════════════════════════════

def conectar_navegador_ja_logado(playwright) -> tuple[BrowserContext, Page]:
    """
    A partir de 29/07/2026: o robô NÃO abre mais o navegador nem faz
    login sozinho. O login inteiro (certificado + qualquer captcha) é
    feito manualmente pelo usuário, num Chrome comum, ANTES de rodar
    este script — o robô só se CONECTA a esse navegador já aberto e já
    autenticado, via depuração remota do Chrome (CDP).

    Pré-requisito: o Chrome precisa ter sido aberto com o parâmetro
    --remote-debugging-port (ver README) — não é o Chrome comum aberto
    clicando no ícone. E o usuário precisa já estar logado no ISS
    Digital (tela de seleção de Contribuinte visível) antes de rodar
    este script.
    """
    try:
        browser = playwright.chromium.connect_over_cdp(
            f"http://localhost:{config.porta_debug_chrome}", timeout=10000
        )
    except Exception as erro:
        logger.error(
            "Não foi possível conectar ao Chrome na porta %d. Confirme que "
            "o Chrome foi aberto com --remote-debugging-port=%d (ver "
            "README) e que você já concluiu o login manualmente. Erro: %s",
            config.porta_debug_chrome, config.porta_debug_chrome, erro,
        )
        raise

    context = browser.contexts[0]

    # Procura, entre as abas já abertas, uma que já esteja no ISS Digital
    # logado; se não achar, usa a aba ativa mesmo assim (o usuário deve
    # ter deixado ela lá).
    pagina_iss = None
    for pagina in context.pages:
        if "app.londrina.pr.gov.br" in pagina.url:
            pagina_iss = pagina
            break

    if pagina_iss is None:
        if not context.pages:
            raise RuntimeError(
                "Nenhuma aba aberta no Chrome conectado. Deixe a aba do "
                "ISS Digital (já logado) aberta antes de rodar o robô."
            )
        pagina_iss = context.pages[0]
        logger.warning(
            "Nenhuma aba em app.londrina.pr.gov.br encontrada — usando a "
            "aba ativa (%s). Confirme que o login já foi concluído.",
            pagina_iss.url,
        )

    if "app.londrina.pr.gov.br" not in pagina_iss.url:
        raise RuntimeError(
            f"A aba conectada não está no ISS Digital (está em "
            f"{pagina_iss.url}). Complete o login manualmente antes de "
            f"rodar o robô."
        )

    logger.info("Conectado ao Chrome já logado — URL atual: %s", pagina_iss.url)
    pagina_iss.set_default_timeout(TIMEOUT_ELEMENTO_MS)
    pagina_iss.set_default_navigation_timeout(TIMEOUT_NAVEGACAO_MS)
    aguardar_portal(pagina_iss, "conexão inicial ao ISS Digital")
    return context, pagina_iss


# ═══════════════════════════════════════════════════════════════════════
# SELEÇÃO DE CONTRIBUINTE
# ═══════════════════════════════════════════════════════════════════════

def selecionar_contribuinte(
    page: Page,
    cliente: ClienteLondrina,
    tentativas: int | None = None,
    timeout_nome_ms: int | None = None,
) -> bool:
    """
    Seleciona a empresa e SÓ retorna True depois de confirmar que o nome do
    contribuinte realmente apareceu na tela. Reforçado em 05/08/2026: a
    troca de empresa era o ponto que mais dava erro — o robô setava o CNPJ
    mas o nome demorava a renderizar e ele seguia cedo demais. Agora:

      1) antes de tudo, checa se a sessão do gov.br caiu (para na hora se
         caiu, em vez de falhar em cascata);
      2) tenta a seleção até `tentativas` vezes, com pausa crescente entre
         as tentativas (dias de lentidão);
      3) depois de cada tentativa, ESPERA o nome/CNPJ aparecer no campo por
         até `timeout_nome_ms` antes de considerar sucesso.

    Os parâmetros `tentativas` e `timeout_nome_ms` são opcionais e caem nos
    padrões do módulo — o modo diagnóstico usa valores menores para não
    gastar minutos em empresas inacessíveis.
    """
    tentativas = tentativas or TENTATIVAS_SELECAO_CONTRIBUINTE
    timeout_nome_ms = timeout_nome_ms or TIMEOUT_NOME_CONTRIBUINTE_MS

    if detectar_sessao_expirada(page):
        raise SessaoExpiradaError(
            f"Sessão do gov.br caiu antes de selecionar {cliente.nome_exibicao}."
        )

    for tentativa in range(1, tentativas + 1):
        _selecionar_contribuinte_uma_vez(page, cliente)

        # Confirmação real: o nome/CNPJ apareceu no campo? (é isso que
        # demora na troca de empresa). Só aqui damos como certo.
        if aguardar_nome_contribuinte(page, cliente, timeout_nome_ms):
            if tentativa > 1:
                logger.info(
                    "Contribuinte %s confirmado na tentativa %d/%d.",
                    cliente.apelido or cliente.codigo, tentativa, tentativas,
                )
            return True

        if detectar_sessao_expirada(page):
            raise SessaoExpiradaError(
                f"Sessão do gov.br caiu ao selecionar {cliente.nome_exibicao}."
            )

        if tentativa < tentativas:
            pausa = PAUSA_ANTES_RETENTAR_SELECAO_MS * tentativa
            logger.warning(
                "Nome do contribuinte %s não apareceu na tentativa %d/%d — "
                "esperando %.1fs e tentando de novo (troca de empresa lenta).",
                cliente.apelido or cliente.codigo, tentativa, tentativas,
                pausa / 1000,
            )
            page.wait_for_timeout(pausa)

    logger.error(
        "Contribuinte %s (%s) não confirmado após %d tentativa(s) — pulando.",
        cliente.apelido or cliente.codigo, formatar_cnpj(cliente.cnpj), tentativas,
    )
    return False


def _selecionar_contribuinte_uma_vez(page: Page, cliente: ClienteLondrina) -> bool:
    """
    UMA tentativa de seleção (a lógica original). Não há busca por CNPJ
    dentro da lista (confirmado pelo usuário) — a estratégia é abrir a lista
    suspensa e localizar o item pelo CNPJ formatado (que sempre aparece no
    início do texto de cada item), deixando o Playwright rolar
    automaticamente até o item ao clicar.

    Corrigido após diagnóstico real (30/07/2026), em duas etapas:
    1) Clicar no CAMPO DE TEXTO não abre a lista — quem abre é o ícone
       de seta (span.icon-popup-lov-under).
    2) O ícone é um INTERRUPTOR (liga/desliga), não um botão só de
       "abrir" — confirmado por print real onde a lista já estava
       aberta ANTES do clique (sobrou de uma tentativa anterior, já que
       agora reaproveitamos a mesma aba entre execuções), e o clique
       FECHOU em vez de abrir. Por isso, agora sempre confere o estado
       real via aria-expanded antes de decidir se clica ou não.
    """
    cnpj_formatado = formatar_cnpj(cliente.cnpj)

    campo = page.locator('input[aria-labelledby="P5_CONTRIBUINTE_LABEL"]')
    try:
        campo.wait_for(state="visible", timeout=TIMEOUT_ELEMENTO_MS)
    except PWTimeout:
        logger.warning(
            "Campo Contribuinte demorou demais para %s — tentando mais uma vez.",
            cliente.apelido,
        )
        page.wait_for_timeout(2500)
        try:
            campo.wait_for(state="visible", timeout=TIMEOUT_ELEMENTO_MS)
        except PWTimeout:
            logger.error("Campo Contribuinte não apareceu para %s.", cliente.apelido)
            return False

    # O portal é Oracle APEX. A lista visual é virtualizada e não mantém
    # todos os 286 itens no DOM; por isso, procurar o CNPJ com get_by_text
    # falhou em 243 clientes no primeiro teste real. A API pública do próprio
    # APEX define o valor do item sem depender da rolagem da lista.
    try:
        resultado_apex = page.evaluate(
            """
            ({cnpj, cnpjFormatado}) => {
                if (!window.apex || typeof window.apex.item !== 'function') {
                    return {ok: false, motivo: 'API APEX indisponível'};
                }
                const item = window.apex.item('P5_CONTRIBUINTE');
                if (!item || typeof item.setValue !== 'function') {
                    return {ok: false, motivo: 'Item P5_CONTRIBUINTE indisponível'};
                }
                item.setValue(cnpj, cnpjFormatado, false);
                return {ok: true, valor: String(item.getValue() || '')};
            }
            """,
            {"cnpj": cliente.cnpj, "cnpjFormatado": cnpj_formatado},
        )
        if resultado_apex.get("ok"):
            try:
                # Corrigido em 03/08/2026: erro real "strict mode violation:
                # locator('.u-Processing') resolved to 2 elements" — a tela
                # de Apurações também usa essa mesma classe para seu próprio
                # indicador, então pode haver mais de um ao mesmo tempo.
                # Espera TODOS ficarem escondidos, não só o primeiro.
                indicadores = page.locator(".u-Processing")
                for indice in range(indicadores.count()):
                    indicadores.nth(indice).wait_for(
                        state="hidden", timeout=TIMEOUT_ELEMENTO_MS
                    )
            except PWTimeout:
                pass
            page.wait_for_timeout(PAUSA_ESTABILIZACAO_MS)
            valor_apex = page.evaluate(
                """() => String(window.apex.item('P5_CONTRIBUINTE').getValue() || '')"""
            )
            data_value = campo.get_attribute("data-value") or ""
            confirmacao = re.sub(r"\D", "", f"{valor_apex} {data_value}")
            if cliente.cnpj in confirmacao:
                logger.info(
                    "Contribuinte confirmado pela API APEX: %s (%s).",
                    cliente.apelido,
                    cnpj_formatado,
                )
                return True
    except Exception as erro:
        logger.warning(
            "Seleção direta pela API APEX falhou para %s: %s. "
            "Tentando a lista visual.",
            cliente.apelido,
            erro,
        )

    # Fallback visual: abre a lista e rola o recipiente virtualizado em
    # etapas, procurando o CNPJ que estiver realmente renderizado.
    try:
        icone_seta = page.locator("span.icon-popup-lov-under")
        if campo.get_attribute("aria-expanded") != "true":
            (icone_seta.first if icone_seta.count() else campo).click()
            page.wait_for_timeout(500)

        recipiente = page.locator(
            '[role="listbox"]:visible, .a-PopupLOV-results:visible, '
            '.ui-dialog-content:visible'
        ).first
        recipiente.wait_for(state="visible", timeout=TIMEOUT_ELEMENTO_MS)

        for _ in range(400):
            itens = page.get_by_text(cnpj_formatado, exact=False)
            for indice in range(itens.count()):
                item = itens.nth(indice)
                if item.is_visible():
                    item.click()
                    aguardar_portal(page, f"confirmação de {cliente.apelido}")
                    data_value = re.sub(
                        r"\D", "", campo.get_attribute("data-value") or ""
                    )
                    if cliente.cnpj in data_value:
                        return True

            movimento = recipiente.evaluate(
                """
                el => {
                    const antes = el.scrollTop;
                    el.scrollTop = Math.min(
                        el.scrollTop + Math.max(el.clientHeight * 0.8, 150),
                        el.scrollHeight
                    );
                    return {antes, depois: el.scrollTop, maximo: el.scrollHeight - el.clientHeight};
                }
                """
            )
            if movimento["depois"] == movimento["antes"]:
                break
            page.wait_for_timeout(100)
    except Exception as erro:
        logger.warning(
            "Fallback visual não localizou %s (%s): %s",
            cliente.apelido,
            cnpj_formatado,
            erro,
        )

    logger.error(
        "Contribuinte %s (%s) não foi confirmado — pulando este cliente.",
        cliente.apelido,
        cnpj_formatado,
    )
    try:
        if campo.get_attribute("aria-expanded") == "true":
            icone_seta = page.locator("span.icon-popup-lov-under")
            if icone_seta.count():
                icone_seta.first.click()
    except Exception:
        pass
    return False


# ═══════════════════════════════════════════════════════════════════════
# NAVEGAÇÃO ENTRE EMITIDAS / RECEBIDAS (sempre por clique — os links têm
# parâmetros de sessão que mudam a cada login, nunca URL fixa)
# ═══════════════════════════════════════════════════════════════════════

def navegar_para_notas(page: Page, tipo: str) -> None:
    """
    Usa "load" em vez de "networkidle" — confirmado por log real
    (29/07/2026) que este portal tem alguma requisição contínua em
    segundo plano que nunca deixa a rede "parada", fazendo networkidle
    estourar o tempo mesmo quando a navegação já tinha completado de
    verdade ("load" disparou normalmente no mesmo log).

    Adicionado/ampliado em 03/08/2026: até 3 tentativas (era 2), com
    tempos de espera bem maiores — confirmado por log real que o portal
    tem momentos de lentidão genuína.

    Reforçado em 03/08/2026 (2ª rodada): antes, "sucesso" só significava
    "o clique não deu erro e a página carregou" — SEM confirmar que a
    URL resultante é realmente a tela certa. Log real mostrou um cliente
    (UNITTE) esperando 3×30s pelo calendário sem nunca aparecer, o que
    só faz sentido se a navegação tiver "funcionado" mas caído em outro
    lugar. Agora confere o trecho da URL específico de cada tela (visto
    em inspeção real: "nfse-emitidas" / "nfs-e-recebidas") antes de dar
    como sucesso — se não bater, TENTA DE NOVO (reabrir o menu, clicar
    de novo), em vez de seguir cego para o calendário.
    """
    nome_link = "NFs-e Emitidas" if tipo == "emitidas" else "NFs-e Recebidas"
    trecho_url_esperado = "nfse-emitidas" if tipo == "emitidas" else "nfs-e-recebidas"

    for tentativa in range(1, TENTATIVAS_PORTAL_LENTO + 1):
        try:
            page.get_by_role("button", name="Consulta NFS-e").click()
            page.get_by_role("link", name=nome_link).click(timeout=TIMEOUT_ELEMENTO_MS)
            page.wait_for_load_state("load", timeout=TIMEOUT_NAVEGACAO_MS)
            aguardar_portal(page, f"abertura de {nome_link}")

            if trecho_url_esperado not in page.url:
                raise RuntimeError(
                    f"Após clicar em '{nome_link}', a URL não é a esperada "
                    f"(esperado conter '{trecho_url_esperado}', obtido: {page.url})"
                )
            return
        except (PWTimeout, RuntimeError) as erro:
            if tentativa == TENTATIVAS_PORTAL_LENTO:
                raise
            logger.warning(
                "Navegação para '%s' não confirmada na tentativa %d/4 (%s) — "
                "tentando de novo.",
                nome_link, tentativa, erro,
            )
            page.wait_for_timeout(2500)


def voltar_pagina_inicial(page: Page, url_pagina_inicial_iss: str) -> None:
    """Volta à tela de contribuintes sem sair do ISS Digital.

    O portal possui mais de um link chamado "Página Inicial" — um do
    ISS Digital (o que queremos) e outro do portal público. No teste
    real de 31/07/2026, o clique por texto simples escolheu o link
    errado (portal.londrina.pr.gov.br), encerrando o fluxo.

    Corrigido em 03/08/2026, em duas rodadas:
    1) Trocado para page.goto() com a URL capturada — mas isso se
       mostrou consistentemente lento/travado (Timeout 15s repetido em
       teste real), mesmo removendo o "?session=..." da URL.
    2) Voltado para clique, mas agora MIRADO pelo aria-label real do
       link certo — "Serviços às empresas" (confirmado por inspeção
       real do HTML no início do projeto: o texto visível é "Página
       Inicial", mas o aria-label é diferente, o que resolve a
       ambiguidade sem precisar de goto() nenhum).
    """
    try:
        page.get_by_role("link", name="Serviços às empresas").click(
            timeout=TIMEOUT_ELEMENTO_MS
        )
        page.wait_for_load_state("load", timeout=TIMEOUT_NAVEGACAO_MS)
        aguardar_portal(page, "retorno à lista de contribuintes")
    except PWTimeout:
        # Fallback: se o aria-label mudou ou não foi encontrado, tenta a
        # navegação direta como antes (mais lenta, mas ainda funcional).
        logger.warning(
            "Link 'Serviços às empresas' não encontrado/clicável — "
            "tentando navegação direta como alternativa."
        )
        url_base = url_pagina_inicial_iss.split("?")[0]
        page.goto(url_base, timeout=TIMEOUT_NAVEGACAO_MS)
        page.wait_for_load_state("load", timeout=TIMEOUT_NAVEGACAO_MS)
        aguardar_portal(page, "retorno direto à lista de contribuintes")

    if (
        "app.londrina.pr.gov.br" not in page.url
        or CAMINHO_PAGINA_INICIAL_ISS not in page.url
    ):
        raise RuntimeError(
            "O retorno não permaneceu na página inicial do ISS Digital "
            f"(URL obtida: {page.url})."
        )


# ═══════════════════════════════════════════════════════════════════════
# CALENDÁRIO — a parte mais delicada
# ═══════════════════════════════════════════════════════════════════════

def _clicar_navegacao_mes(page: Page, vezes: int) -> None:
    """
    Clica "Próximo Mês" (se vezes > 0) ou "Mês Anterior" (se vezes < 0)
    o número de vezes necessário para sair do mês atual real (o
    calendário sempre abre nele) até o mês alvo.
    """
    if vezes == 0:
        return

    if vezes > 0:
        botao = page.get_by_role("button", name="Próximo Mês")
        repeticoes = vezes
    else:
        # TODO: aria-label "Mês Anterior" é inferido por simetria, não
        # confirmado diretamente — fallback para o ícone se não achar
        try:
            botao = page.get_by_role("button", name="Mês Anterior")
            botao.wait_for(timeout=2000)
        except PWTimeout:
            botao = page.locator("span.icon-prev").locator("..")
        repeticoes = -vezes

    for _ in range(repeticoes):
        botao.click()
        page.wait_for_timeout(300)  # pequena pausa para o calendário atualizar


def _clicar_dia(page: Page, dia: int) -> None:
    """
    TODO — HEURÍSTICA NÃO CONFIRMADA AO VIVO: quando o número do dia
    aparece mais de uma vez no calendário (dias esmaecidos do mês
    anterior/seguinte compartilhando número com dias do mês alvo), a
    ocorrência correta nem sempre é a primeira. Heurística aplicada:
    dias baixos (prováveis de colidir com o FIM do mês seguinte, exibido
    depois no DOM) usam a primeira ocorrência; dias altos (prováveis de
    colidir com o FIM do mês anterior, exibido antes no DOM) usam a
    última ocorrência. Se isso selecionar o dia errado no teste ao vivo,
    é o ponto mais provável a precisar ajuste — me avise o resultado.
    """
    candidatos = page.locator(f'span[role="link"]:text-is("{dia}")')
    total = candidatos.count()

    if total == 0:
        raise ValueError(f"Dia {dia} não encontrado no calendário visível.")
    elif total == 1:
        candidatos.first.click()
    else:
        if dia <= 15:
            candidatos.first.click()
        else:
            candidatos.last.click()


def preencher_data_no_calendario(page: Page, campo_id: str, data_alvo: date) -> None:
    """
    Abre o calendário associado ao campo (via botão com aria-controls
    apontando para o id do campo), navega até o mês/ano alvo a partir do
    mês atual real, e clica no dia.

    Ampliado em 03/08/2026: até 3 tentativas (era 2) com tempos de
    espera maiores — confirmado por log real que o portal tem momentos
    de lentidão genuína o suficiente para estourar 15s repetidamente.
    """
    hoje = date.today()
    diferenca_meses = (data_alvo.year - hoje.year) * 12 + (data_alvo.month - hoje.month)

    botao_calendario = page.locator(f'button[aria-controls="{campo_id}"]')
    ultimo_erro = None
    for tentativa in range(1, TENTATIVAS_PORTAL_LENTO + 1):
        try:
            botao_calendario.wait_for(state="visible", timeout=TIMEOUT_ELEMENTO_MS)
            ultimo_erro = None
            break
        except PWTimeout as erro:
            ultimo_erro = erro
            logger.warning(
                "Botão do calendário (%s) demorou demais na tentativa %d/4 — "
                "tentando de novo.",
                campo_id, tentativa,
            )
            page.wait_for_timeout(2500)
    if ultimo_erro is not None:
        raise ultimo_erro

    botao_calendario.click()
    page.wait_for_timeout(700)  # tempo do calendário abrir

    _clicar_navegacao_mes(page, diferenca_meses)
    _clicar_dia(page, data_alvo.day)
    page.wait_for_timeout(300)


def filtrar_por_periodo(page: Page, tipo: str, data_inicial: date, data_final: date) -> None:
    prefixo = "P27" if tipo == "emitidas" else "P29"
    campo_data_inicio = f"{prefixo}_DATA_INICIO_input"
    campo_data_fim = f"{prefixo}_DATA_FIM_input"

    preencher_data_no_calendario(page, campo_data_inicio, data_inicial)
    preencher_data_no_calendario(page, campo_data_fim, data_final)

    # Confirma que os campos realmente ficaram com a data pedida antes de
    # clicar em Atualizar — adicionado após auditoria externa (achado
    # C-04): a escolha do dia no calendário usa uma heurística não
    # confirmada ao vivo; se ela errar, é melhor travar aqui do que
    # seguir com data errada silenciosamente.
    esperado_inicio = data_inicial.strftime("%d/%m/%Y")
    esperado_fim = data_final.strftime("%d/%m/%Y")
    valor_inicio = page.locator(f"#{campo_data_inicio}").input_value()
    valor_fim = page.locator(f"#{campo_data_fim}").input_value()
    if valor_inicio != esperado_inicio or valor_fim != esperado_fim:
        raise ValueError(
            f"Data preenchida não confere com a esperada — "
            f"início: esperado='{esperado_inicio}' obtido='{valor_inicio}', "
            f"fim: esperado='{esperado_fim}' obtido='{valor_fim}'. "
            f"Abortando este cliente (heurística do calendário pode ter "
            f"clicado no dia errado)."
        )

    page.get_by_role("button", name="Atualizar").click()

    # NOTA HONESTA (achado C-03 da auditoria externa): "Atualizar" nesta
    # tela provavelmente dispara uma atualização via AJAX (padrão comum
    # em Oracle APEX), não uma navegação de página inteira — o que
    # significa que wait_for_load_state("load") pode resolver
    # IMEDIATAMENTE sem esperar a atualização de verdade terminar. Uma
    # tentativa curta de networkidle é feita como sinal adicional (sem
    # ser bloqueante, já que sabemos que esse portal às vezes nunca
    # fica realmente ocioso), somada a uma pausa maior. Isto NÃO é uma
    # correção definitiva — precisa de inspeção ao vivo pra identificar
    # o sinal exato (ex.: um spinner específico) de que a atualização
    # terminou.
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except PWTimeout:
        pass
    page.wait_for_timeout(2000)
    aguardar_portal(
        page,
        f"atualização das notas {tipo} de {esperado_inicio} a {esperado_fim}",
    )


# ═══════════════════════════════════════════════════════════════════════
# DOWNLOAD (fluxo de dois cliques confirmado)
# ═══════════════════════════════════════════════════════════════════════

def _hash_arquivo(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def contar_xmls_no_zip(caminho: Path) -> int:
    """Valida o ZIP e conta somente arquivos XML existentes dentro dele.

    Um ZIP válido com zero XML significa *sem movimento* e NÃO é erro.
    Arquivo ausente, ZIP inválido ou entrada corrompida é falha técnica e
    deve permanecer na lista de pendências.
    """
    if not caminho.exists():
        raise FileNotFoundError(f"ZIP não encontrado: {caminho}")
    if not zipfile.is_zipfile(caminho):
        raise RuntimeError(f"Arquivo não é um ZIP válido: {caminho.name}")
    with zipfile.ZipFile(caminho) as zf:
        entrada_com_erro = zf.testzip()
        if entrada_com_erro is not None:
            raise RuntimeError(
                f"ZIP corrompido ({caminho.name}) — entrada com falha: {entrada_com_erro}"
            )
        return sum(
            1
            for info in zf.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".xml")
        )


def avaliar_zips_existentes(caminhos: list[Path]) -> tuple[str, int, str]:
    """Retorna (OK/X, quantidade de XMLs, explicação) para ZIPs válidos.

    Usada também pelo verificador de pendências, para que a regra seja
    idêntica tanto durante o download quanto numa conferência posterior.
    """
    if not caminhos:
        raise FileNotFoundError("nenhum ZIP encontrado no disco")
    contagens_validas: list[int] = []
    invalidos: list[str] = []
    for caminho in caminhos:
        try:
            contagens_validas.append(contar_xmls_no_zip(caminho))
        except Exception as erro:
            invalidos.append(f"{caminho.name}: {erro}")
    if not contagens_validas:
        raise RuntimeError("nenhum ZIP válido; " + "; ".join(invalidos))
    total_xml = sum(contagens_validas)
    aviso_invalidos = (
        f"; {len(invalidos)} arquivo(s) inválido(s) ignorado(s)" if invalidos else ""
    )
    if total_xml:
        return "OK", total_xml, f"{total_xml} XML(s) encontrado(s) dentro do ZIP{aviso_invalidos}"
    return "X", 0, f"ZIP válido, mas sem XML (sem movimento){aviso_invalidos}"


def _mover_sem_sobrescrever(origem: Path, destino: Path) -> Path:
    """
    Move o arquivo para o destino sem nunca sobrescrever. Se já existe um
    arquivo idêntico (mesmo hash), descarta o novo (era o mesmo
    conteúdo). Se existe um arquivo DIFERENTE com o mesmo nome, versiona
    com sufixo. Adicionado após auditoria externa (28/07/2026) apontar
    que reprocessar a mesma competência, ou baixar mais de um bloco para
    o mesmo cliente/tipo, sobrescrevia o ZIP anterior silenciosamente.
    """
    if destino.exists():
        if _hash_arquivo(destino) == _hash_arquivo(origem):
            origem.unlink()  # já existe idêntico — descarta a cópia temporária
            return destino
        contador = 2
        while True:
            candidato = destino.with_name(f"{destino.stem}__v{contador}{destino.suffix}")
            if not candidato.exists():
                destino = candidato
                break
            contador += 1

    shutil.move(str(origem), str(destino))
    return destino


def baixar_zip_xmls(page: Page) -> Path | None:
    """
    Retorna o caminho do ZIP baixado, ou None quando o portal indica
    EXPLICITAMENTE que não há nota no período (sem movimento).

    Ajustado em 05/08/2026 a pedido do usuário ("erro é só erro mesmo"):
    antes, se o botão XMLS não aparecesse, era sempre tratado como falha
    técnica (ERRO) — inclusive em empresas que simplesmente não tinham nota.
    Agora, se o botão não aparece MAS a tela mostra a mensagem de "sem
    registro/sem NFS-e", isso é sem movimento (X), não ERRO. Só é ERRO
    quando o botão some sem nenhuma explicação de tela vazia.
    A decisão final entre OK e X (quando HÁ ZIP) segue sendo feita depois,
    olhando os XMLs dentro do ZIP.
    """
    try:
        botoes_xml = page.get_by_role("button", name="XMLS", exact=True)
        botoes_xml.first.wait_for(state="visible", timeout=TIMEOUT_ELEMENTO_MS)
        botoes_xml.first.click()
    except PWTimeout:
        if pagina_indica_sem_movimento(page):
            logger.info(
                "Botão XMLS ausente, mas a tela indica período sem nota — "
                "tratado como SEM MOVIMENTO (X), não como erro."
            )
            return None
        raise RuntimeError(
            "Botão XMLS não apareceu em 120s e a tela não indicou 'sem "
            "registro'. Tratado como falha técnica (ficará pendente)."
        )

    botao_confirmar = page.locator("button.js-confirmBtn:visible")
    botao_confirmar.wait_for(state="visible", timeout=TIMEOUT_ELEMENTO_MS)
    with page.expect_download(timeout=TIMEOUT_DOWNLOAD_MS) as info:
        botao_confirmar.click()

    download = info.value
    caminho_temp = Path.cwd() / "_tmp_downloads_londrina" / download.suggested_filename
    caminho_temp.parent.mkdir(exist_ok=True)
    download.save_as(caminho_temp)
    aguardar_portal(page, "finalização do download")

    # Validação adicionada após auditoria externa (achado C-06): nunca
    # aceitar o arquivo baixado como ZIP válido sem checar de verdade —
    # o portal pode retornar uma página de erro em HTML com nome de .zip.
    contar_xmls_no_zip(caminho_temp)

    return caminho_temp


def baixar_excel_conferencia(page: Page) -> Path | None:
    """
    Baixa o relatório Excel de conferência (botão id="BTN_EXCEL",
    confirmado por inspeção real em 03/08/2026) — mesma tela do botão
    XMLS, mesmo cliente/tipo/período já filtrado. Segue o mesmo padrão
    de baixar_zip_xmls: None só com confirmação explícita de "sem
    notas"; qualquer outra falha levanta exceção.

    NÃO CONFIRMADO ao vivo: se existe um diálogo de confirmação (como o
    "js-confirmBtn" do XMLS) depois de clicar no Excel. Tenta primeiro
    um download direto; se não vier em 15s, tenta achar e clicar num
    possível diálogo de confirmação como plano B.
    """
    try:
        botao_excel = page.locator("#BTN_EXCEL")
        botao_excel.wait_for(state="visible", timeout=TIMEOUT_ELEMENTO_MS)
    except PWTimeout:
        indicadores_vazio = ["Nenhum registro encontrado", "Nenhum dado encontrado", "sem registros"]
        for texto in indicadores_vazio:
            if page.get_by_text(texto, exact=False).count() > 0:
                logger.info("Confirmado 'sem notas' no período (indicador: '%s') — sem Excel a baixar.", texto)
                return None
        raise RuntimeError(
            "Botão Excel (#BTN_EXCEL) não apareceu em 120s e nenhum indicador "
            "explícito de 'sem notas' foi encontrado — tratado como falha técnica."
        )

    try:
        with page.expect_download(timeout=15000) as info:
            botao_excel.click()
    except PWTimeout:
        # Plano B: pode haver diálogo de confirmação como o do XMLS —
        # não confirmado ao vivo, tentativa defensiva.
        logger.warning(
            "Download do Excel não iniciou em 15s após o clique — "
            "tentando localizar um possível diálogo de confirmação."
        )
        botao_confirmar = page.locator("button.js-confirmBtn:visible")
        botao_confirmar.wait_for(state="visible", timeout=TIMEOUT_ELEMENTO_MS)
        with page.expect_download(timeout=TIMEOUT_DOWNLOAD_MS) as info:
            botao_confirmar.click()

    download = info.value
    caminho_temp = Path.cwd() / "_tmp_downloads_londrina" / download.suggested_filename
    caminho_temp.parent.mkdir(exist_ok=True)
    download.save_as(caminho_temp)
    aguardar_portal(page, "finalização do download do Excel")

    # .xlsx é, por baixo dos panos, um arquivo ZIP (Office Open XML) —
    # a mesma checagem de integridade usada para os ZIPs de nota serve
    # aqui também.
    if not zipfile.is_zipfile(caminho_temp):
        raise RuntimeError(
            f"Arquivo Excel baixado ({caminho_temp.name}) não parece válido — "
            f"o portal provavelmente retornou um erro em vez do arquivo."
        )

    return caminho_temp


# ═══════════════════════════════════════════════════════════════════════
# ORQUESTRAÇÃO POR CLIENTE
# ═══════════════════════════════════════════════════════════════════════

class NavegadorFechadoError(Exception):
    """
    Levantada quando o navegador/página fecha inesperadamente. Diferente
    de outros erros (que afetam só 1 cliente), este é FATAL para a
    execução inteira — não faz sentido continuar tentando os próximos
    283 clientes se o navegador já não existe mais.
    """


def _salvar_print_diagnostico(page: Page, cliente: "ClienteLondrina") -> None:
    """
    Print automático em toda falha de cliente (adicionado em 03/08/2026,
    a pedido do usuário) — sem isso, cada erro novo exigia mais uma
    rodada pedindo print manual. Nunca levanta exceção — se falhar em
    salvar o print, só avisa e segue (não pode derrubar o robô por
    causa de um print que não deu certo).
    """
    try:
        pasta_diagnostico = Path("diagnosticos_falha")
        pasta_diagnostico.mkdir(exist_ok=True)
        marca_tempo = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_arquivo = f"falha_{cliente.codigo}_{cliente.apelido or 'sem_nome'}_{marca_tempo}.png"
        nome_arquivo = re.sub(r'[<>:"/\\|?*]', "_", nome_arquivo)  # remove caracteres inválidos de nome de arquivo no Windows
        caminho_print = pasta_diagnostico / nome_arquivo
        page.screenshot(path=str(caminho_print), full_page=True)
        logger.error("Print da tela no momento da falha salvo em: %s", caminho_print.resolve())
    except Exception:
        logger.warning("Não foi possível salvar o print de diagnóstico desta falha.")


def processar_cliente(
    page: Page, cliente: ClienteLondrina, blocos_periodo: list[tuple[date, date]],
    competencia_mmaaaa: str,
    pastas_clientes_emitidas: dict[str, PastaDominio],
    pastas_clientes_tomados: dict[str, PastaDominio],
    pastas_clientes_relatorios: dict[str, PastaDominio],
    raiz_relatorios: Path,
    url_pagina_inicial_iss: str,
) -> ResultadoCliente:
    """Processa os dois tipos sem confundir falta de XML com falha.

    Cada ZIP é salvo mesmo quando estiver vazio. Depois de salvo, o robô
    abre o ZIP: pelo menos um XML = OK; zero XML = X (sem movimento);
    download ausente/corrompido = ERRO e entra nas pendências.

    Emitidas e recebidas são independentes: se uma pasta ou download
    falhar, o robô ainda tenta concluir o outro tipo e registra exatamente
    o que ocorreu na planilha de controle.
    """
    pasta_competencia_emitidas = obter_pasta_competencia(
        cliente, pastas_clientes_emitidas, competencia_mmaaaa
    )
    pasta_competencia_tomados = obter_pasta_competencia(
        cliente, pastas_clientes_tomados, competencia_mmaaaa
    )
    destinos_por_tipo = {
        "emitidas": pasta_competencia_emitidas,
        "recebidas": pasta_competencia_tomados,
    }
    status_tipo = {"emitidas": "", "recebidas": ""}
    quantidade_xml = {"emitidas": None, "recebidas": None}
    erros: list[str] = []
    arquivos_salvos: list[str] = []
    relatorios_salvos = 0
    erros_relatorio: list[str] = []

    for tipo, destino in destinos_por_tipo.items():
        if destino is None:
            status_tipo[tipo] = "ERRO"
            nome_raiz = "Importação" if tipo == "emitidas" else "Importação - Tomados"
            erros.append(
                f"{tipo}: pasta do cliente {cliente.codigo} não encontrada em {nome_raiz}"
            )

    if all(destino is None for destino in destinos_por_tipo.values()):
        detalhe = "; ".join(erros)
        return ResultadoCliente(
            codigo_cliente=cliente.codigo,
            nome=cliente.nome_exibicao,
            cnpj=cliente.cnpj,
            status="pendencia_sem_pasta",
            detalhe=detalhe,
            emitidos="ERRO",
            recebidos="ERRO",
            relatorio="ERRO",
        )

    try:
        page.bring_to_front()
        aguardar_portal(page, f"início do cliente {cliente.nome_exibicao}")
        if not selecionar_contribuinte(page, cliente):
            _salvar_print_diagnostico(page, cliente)
            return ResultadoCliente(
                codigo_cliente=cliente.codigo,
                nome=cliente.nome_exibicao,
                cnpj=cliente.cnpj,
                status="falha",
                detalhe="Contribuinte não encontrado/confirmado na lista",
                emitidos="ERRO" if not status_tipo["emitidas"] else status_tipo["emitidas"],
                recebidos="ERRO" if not status_tipo["recebidas"] else status_tipo["recebidas"],
                relatorio="ERRO",
            )

        pasta_competencia_relatorio = obter_pasta_competencia_relatorio(
            cliente, pastas_clientes_relatorios, raiz_relatorios, competencia_mmaaaa
        )
        precisa_reselecionar = False

        for tipo in ("emitidas", "recebidas"):
            pasta_competencia = destinos_por_tipo[tipo]
            if pasta_competencia is None:
                continue

            try:
                if precisa_reselecionar:
                    voltar_pagina_inicial(page, url_pagina_inicial_iss)
                    if not selecionar_contribuinte(page, cliente):
                        raise RuntimeError(
                            "não foi possível selecionar novamente o contribuinte após a falha anterior"
                        )
                    precisa_reselecionar = False

                total_xml_tipo = 0
                reutilizar_zip = False
                if len(blocos_periodo) == 1 and pasta_competencia.exists():
                    prefixo = "xml_nfse_emitidas" if tipo == "emitidas" else "xml_nfse_recebidas"
                    existentes = sorted(
                        pasta_competencia.glob(f"{prefixo}_{cliente.cnpj}*.zip")
                    )
                    if existentes:
                        try:
                            _, total_xml_tipo, explicacao = avaliar_zips_existentes(existentes)
                            reutilizar_zip = True
                            logger.info(
                                "%s já possui ZIP válido no disco — download reaproveitado (%s).",
                                tipo.capitalize(), explicacao,
                            )
                        except Exception as erro_existente:
                            logger.warning(
                                "ZIP existente de %s não pôde ser reaproveitado (%s) — "
                                "será feito um novo download.", tipo, erro_existente,
                            )

                navegar_para_notas(page, tipo)
                for indice_bloco, (data_inicial, data_final) in enumerate(blocos_periodo, start=1):
                    filtrar_por_periodo(page, tipo, data_inicial, data_final)
                    if not reutilizar_zip:
                        caminho_zip = baixar_zip_xmls(page)
                        if caminho_zip is None:
                            # Sem movimento CONFIRMADO pela tela (sem nota no
                            # período). Não há ZIP para salvar; conta 0 e segue
                            # — vai virar 'X', nunca 'ERRO'.
                            logger.info(
                                "%s: período sem nota (sem movimento) — nada a baixar.",
                                tipo.capitalize(),
                            )
                        else:
                            total_xml_tipo += contar_xmls_no_zip(caminho_zip)

                            # Nome único por bloco quando há mais de um bloco no
                            # mesmo cliente/tipo.
                            if len(blocos_periodo) > 1:
                                nome_final = f"{caminho_zip.stem}_bloco{indice_bloco}{caminho_zip.suffix}"
                            else:
                                nome_final = caminho_zip.name
                            pasta_competencia.mkdir(exist_ok=True)
                            destino = _mover_sem_sobrescrever(caminho_zip, pasta_competencia / nome_final)
                            arquivos_salvos.append(destino.name)
                            logger.info("Salvo: %s", destino)

                    try:
                        caminho_excel = baixar_excel_conferencia(page)
                        if caminho_excel is not None:
                            if len(blocos_periodo) > 1:
                                nome_excel = f"{caminho_excel.stem}_bloco{indice_bloco}{caminho_excel.suffix}"
                            else:
                                nome_excel = caminho_excel.name
                            pasta_competencia_relatorio.mkdir(exist_ok=True)
                            destino_excel = _mover_sem_sobrescrever(
                                caminho_excel, pasta_competencia_relatorio / nome_excel
                            )
                            arquivos_salvos.append(destino_excel.name)
                            relatorios_salvos += 1
                            logger.info("Salvo: %s", destino_excel)
                    except Exception as erro_excel:
                        mensagem = f"relatório {tipo}: {erro_excel}"
                        erros_relatorio.append(mensagem)
                        logger.error("Falha em %s — ficará pendente.", mensagem)

                quantidade_xml[tipo] = total_xml_tipo
                status_tipo[tipo] = "OK" if total_xml_tipo > 0 else "X"
                logger.info(
                    "%s: %s (%d XML(s) dentro do(s) ZIP(s)).",
                    tipo.capitalize(), status_tipo[tipo], total_xml_tipo,
                )
            except SessaoExpiradaError:
                # Sessão caiu: é fatal para a rodada inteira, não é falha só
                # deste tipo/cliente. Deixa subir para o laço principal parar.
                raise
            except Exception as erro_tipo:
                if page.is_closed() or "Target page, context or browser has been closed" in str(erro_tipo):
                    raise NavegadorFechadoError(
                        f"Navegador fechado durante o processamento de {cliente.nome_exibicao}."
                    ) from erro_tipo
                # Se a sessão caiu no meio deste tipo, para tudo em vez de
                # marcar ERRO e seguir para o próximo (evita cascata).
                if detectar_sessao_expirada(page):
                    raise SessaoExpiradaError(
                        f"Sessão do gov.br caiu durante {tipo} de {cliente.nome_exibicao}."
                    ) from erro_tipo
                status_tipo[tipo] = "ERRO"
                erros.append(f"{tipo}: {erro_tipo}")
                precisa_reselecionar = True
                logger.exception("Falha técnica em %s para %s", tipo, cliente.nome_exibicao)
                _salvar_print_diagnostico(page, cliente)

        # ── Rede de segurança para RECEBIDAS vazia (pedido em 05/08/2026) ──
        # Como o escritório emite nota para quase todos os clientes, uma
        # empresa com "recebidas = 0" é suspeita e, na prática, costuma ser
        # um soluço de download — não ausência real. Então, se recebidas veio
        # vazia (X) mas emitidas foi processada sem erro (sinal de que a
        # sessão e o portal estão vivos), o robô entra de novo na aba de
        # recebidas UMA vez e reconfere, antes de aceitar o vazio. Se ainda
        # vier vazio, aceita como X (nunca vira ERRO) e apenas deixa um aviso
        # na observação para conferência manual. Só no caso de 1 bloco (mês
        # único), para não repetir download em consultas de vários blocos.
        recebidas_vazia = (
            status_tipo["recebidas"] == "X"
            and destinos_por_tipo["recebidas"] is not None
            and status_tipo["emitidas"] != "ERRO"
            and len(blocos_periodo) == 1
            and not precisa_reselecionar
        )
        if recebidas_vazia and not detectar_sessao_expirada(page):
            data_ini, data_fim = blocos_periodo[0]
            try:
                logger.info(
                    "Recebidas veio vazia para %s — reconferindo a aba uma "
                    "segunda vez (escritório costuma ter notas recebidas).",
                    cliente.nome_exibicao,
                )
                navegar_para_notas(page, "recebidas")
                filtrar_por_periodo(page, "recebidas", data_ini, data_fim)
                caminho_zip_rec = baixar_zip_xmls(page)
                if caminho_zip_rec is not None:
                    qtd_rec = contar_xmls_no_zip(caminho_zip_rec)
                    if qtd_rec > 0:
                        pasta_rec = destinos_por_tipo["recebidas"]
                        pasta_rec.mkdir(exist_ok=True)
                        destino_rec = _mover_sem_sobrescrever(
                            caminho_zip_rec, pasta_rec / caminho_zip_rec.name
                        )
                        arquivos_salvos.append(destino_rec.name)
                        quantidade_xml["recebidas"] = qtd_rec
                        status_tipo["recebidas"] = "OK"
                        logger.info(
                            "Reconferência achou %d nota(s) recebida(s) que a "
                            "primeira passada não pegou. Salvo: %s",
                            qtd_rec, destino_rec,
                        )
                    else:
                        caminho_zip_rec.unlink(missing_ok=True)
                if status_tipo["recebidas"] == "X":
                    observacao_recebidas_vazia = (
                        "Recebidas confirmada vazia após 2 tentativas — "
                        "confira manualmente se essa empresa realmente não "
                        "recebeu nota no período."
                    )
                else:
                    observacao_recebidas_vazia = ""
            except Exception as erro_reconf:
                # Reconferência é só um bônus: se falhar, NÃO transforma o X
                # legítimo em erro — mantém o X e registra o aviso.
                logger.warning(
                    "Reconferência de recebidas falhou para %s (%s) — "
                    "mantendo o resultado vazio (X), sem marcar erro.",
                    cliente.nome_exibicao, erro_reconf,
                )
                observacao_recebidas_vazia = (
                    "Recebidas veio vazia e a reconferência não completou — "
                    "confira manualmente."
                )
        else:
            observacao_recebidas_vazia = ""

        voltar_pagina_inicial(page, url_pagina_inicial_iss)
        houve_movimento = "OK" in status_tipo.values()
        if erros_relatorio:
            status_relatorio = "ERRO"
        elif relatorios_salvos:
            status_relatorio = "OK"
        elif houve_movimento:
            status_relatorio = "ERRO"
            erros_relatorio.append(
                "relatório: houve movimento, mas nenhum Excel foi salvo"
            )
        else:
            status_relatorio = "X"
        todos_erros = erros + erros_relatorio
        status_geral = "falha" if todos_erros else "processado"
        resumo = (
            f"Emitidos: {status_tipo['emitidas']}"
            + (f" ({quantidade_xml['emitidas']} XML)" if quantidade_xml['emitidas'] is not None else "")
            + f"; Recebidos: {status_tipo['recebidas']}"
            + (f" ({quantidade_xml['recebidas']} XML)" if quantidade_xml['recebidas'] is not None else "")
            + f"; Relatório: {status_relatorio}"
        )
        if todos_erros:
            resumo += "; " + "; ".join(todos_erros)
        # Aviso (não é erro) quando recebidas ficou vazia mesmo após reconferir.
        if observacao_recebidas_vazia:
            resumo += "; " + observacao_recebidas_vazia
        return ResultadoCliente(
            codigo_cliente=cliente.codigo,
            nome=cliente.nome_exibicao,
            cnpj=cliente.cnpj,
            status=status_geral,
            detalhe=resumo,
            emitidos=status_tipo["emitidas"],
            recebidos=status_tipo["recebidas"],
            relatorio=status_relatorio,
            xml_emitidos=quantidade_xml["emitidas"],
            xml_recebidos=quantidade_xml["recebidas"],
        )

    except SessaoExpiradaError:
        # Sessão do gov.br caiu: fatal para a rodada inteira. Sobe para o
        # laço principal parar de vez, em vez de gastar minutos por empresa.
        raise
    except Exception as erro:
        # Se o navegador/página fechou, não adianta tentar recuperar nem
        # seguir para o próximo cliente — é fatal para a execução inteira.
        if page.is_closed() or "Target page, context or browser has been closed" in str(erro):
            raise NavegadorFechadoError(
                f"Navegador fechado durante o processamento de {cliente.nome_exibicao}. "
                f"Abra o Chrome de novo (ver README), faça login, e rode o robô "
                f"novamente a partir deste cliente."
            ) from erro

        # Sessão caiu de forma menos óbvia (sem fechar o navegador): também
        # é fatal — para tudo em vez de seguir marcando ERRO em cascata.
        if detectar_sessao_expirada(page):
            raise SessaoExpiradaError(
                f"Sessão do gov.br caiu durante {cliente.nome_exibicao}."
            ) from erro

        logger.exception("Erro inesperado processando %s", cliente.nome_exibicao)

        # Adicionado em 03/08/2026, a pedido do usuário ("passa um pente
        # fino"): print automático em TODA falha de cliente, não só no
        # login. Sem isso, cada erro novo exigia mais uma rodada pedindo
        # print manual — agora a evidência visual já vem junto no
        # primeiro log, mesmo que o robô continue sozinho depois.
        _salvar_print_diagnostico(page, cliente)

        # Recuperação em camadas, ampliada em 03/08/2026 após log real
        # mostrar falhas em cadeia (uma falha de recuperação contaminando
        # vários clientes seguintes): a 2ª tentativa antes chamava a
        # MESMA função de novo (não era realmente diferente) — agora a
        # 3ª tentativa faz algo genuinamente mais forte: um reload
        # completo da página antes de desistir.
        recuperacao_ok = False
        try:
            voltar_pagina_inicial(page, url_pagina_inicial_iss)
            recuperacao_ok = True
        except Exception:
            logger.warning(
                "1ª recuperação falhou para %s — aguardando e tentando de novo.",
                cliente.nome_exibicao,
            )
            page.wait_for_timeout(3000)
            try:
                voltar_pagina_inicial(page, url_pagina_inicial_iss)
                recuperacao_ok = True
            except Exception:
                logger.warning(
                    "2ª recuperação falhou para %s — tentando recarregar a "
                    "página por completo como último recurso.",
                    cliente.nome_exibicao,
                )
                try:
                    page.reload(timeout=TIMEOUT_NAVEGACAO_MS)
                    page.wait_for_load_state("load", timeout=TIMEOUT_NAVEGACAO_MS)
                    aguardar_portal(page, "recuperação da tela após falha")
                    voltar_pagina_inicial(page, url_pagina_inicial_iss)
                    recuperacao_ok = True
                except Exception:
                    logger.error(
                        "Não foi possível recuperar a página após a falha em %s — "
                        "o próximo cliente pode ser afetado. Considere reiniciar "
                        "o robô a partir daqui.",
                        cliente.nome_exibicao,
                    )

        detalhe = str(erro) if recuperacao_ok else f"{erro} [RECUPERAÇÃO FALHOU — reinicie o robô]"
        return ResultadoCliente(
            codigo_cliente=cliente.codigo,
            nome=cliente.nome_exibicao,
            cnpj=cliente.cnpj,
            status="falha",
            detalhe=detalhe,
            emitidos=status_tipo["emitidas"] or "ERRO",
            recebidos=status_tipo["recebidas"] or "ERRO",
            relatorio="ERRO",
            xml_emitidos=quantidade_xml["emitidas"],
            xml_recebidos=quantidade_xml["recebidas"],
        )


def imprimir_relatorio(resultados: list[ResultadoCliente], competencia: str) -> Path | None:
    print("\n" + "=" * 60)
    print(f"ROBÔ LONDRINA (ISS DIGITAL) — Competência: {competencia}")
    print("=" * 60)
    for r in resultados:
        print(
            f"  [{r.codigo_cliente}] {r.nome}: {r.status} | "
            f"Emitidos={r.emitidos or '-'} | Recebidos={r.recebidos or '-'} | "
            f"Relatório={r.relatorio or '-'} — {r.detalhe}"
        )
    print("=" * 60 + "\n")

    return salvar_pendencias_csv(resultados, competencia)


def salvar_pendencias_csv(resultados: list[ResultadoCliente], competencia: str) -> Path | None:
    """
    Salva em CSV todo cliente que NÃO terminou como "processado" —
    adicionado em 03/08/2026, a pedido do usuário, para permitir
    reprocessar só quem falhou (com --retomar-pendencias), em vez de
    rodar todos de novo. Retorna None se não houver nenhuma pendência.
    """
    pendencias = [r for r in resultados if r.status != "processado"]
    if not pendencias:
        return None

    caminho = Path(f"pendencias_{competencia}.csv")
    with open(caminho, "w", newline="", encoding="utf-8") as arquivo:
        campos = [
            "codigo_cliente", "nome", "cnpj", "status", "emitidos",
            "recebidos", "relatorio", "xml_emitidos", "xml_recebidos", "detalhe",
        ]
        escritor = csv.DictWriter(arquivo, fieldnames=campos)
        escritor.writeheader()
        for r in pendencias:
            escritor.writerow({
                "codigo_cliente": r.codigo_cliente,
                "nome": r.nome,
                "cnpj": r.cnpj,
                "status": r.status,
                "emitidos": r.emitidos,
                "recebidos": r.recebidos,
                "relatorio": r.relatorio,
                "xml_emitidos": r.xml_emitidos,
                "xml_recebidos": r.xml_recebidos,
                "detalhe": r.detalhe,
            })

    logger.info(
        "%d pendência(s) salva(s) em: %s — use --retomar-pendencias %s "
        "para reprocessar só esses clientes.",
        len(pendencias), caminho.resolve(), caminho,
    )
    return caminho


def carregar_codigos_pendentes(caminho_csv: str) -> set[str]:
    caminho = Path(caminho_csv)
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo de pendências não encontrado: {caminho}")
    with open(caminho, newline="", encoding="utf-8") as arquivo:
        return {linha["codigo_cliente"].strip() for linha in csv.DictReader(arquivo)}


# ═══════════════════════════════════════════════════════════════════════
# MODO DIAGNÓSTICO (--teste-geral): descobre o PERFIL de cada empresa sem
# baixar nada nem mexer na planilha. Serve para achar, de uma vez, quem
# está sem procuração/sem acesso, quem trava numa aba, etc.
# ═══════════════════════════════════════════════════════════════════════

# No diagnóstico não vale a pena esperar 45s × 3 numa empresa que talvez
# nem tenha procuração — usa esperas menores para varrer a lista rápido.
TENTATIVAS_SELECAO_DIAGNOSTICO = 2
TIMEOUT_NOME_DIAGNOSTICO_MS = 15_000


def _testar_abas_acessiveis(page: Page) -> tuple[bool, str]:
    """
    Depois de selecionar a empresa, tenta abrir Emitidas e Recebidas só para
    confirmar que as abas respondem. NÃO filtra período nem baixa nada.
    Retorna (ok, detalhe).
    """
    for tipo, rotulo in (("emitidas", "Emitidas"), ("recebidas", "Recebidas")):
        try:
            navegar_para_notas(page, tipo)
        except Exception as erro:
            return False, f"abriu a seleção mas travou na aba {rotulo}: {erro}"
    return True, "seleção e abas (Emitidas/Recebidas) OK"


def diagnosticar_cliente(page: Page, cliente: ClienteLondrina, url_inicial: str) -> dict:
    """
    Classifica UMA empresa em um dos perfis, sem baixar nada:
      OK               -> seleciona e as duas abas abrem
      SEM ACESSO       -> não dá para selecionar (provável falta de
                          procuração/empresa fora da lista do escritório)
      ERRO NA ABA      -> seleciona, mas uma aba trava (glitch do portal)
      ERRO             -> qualquer outra falha inesperada
    Deixa o gov.br subir como SessaoExpiradaError (o chamador para tudo).
    """
    if detectar_sessao_expirada(page):
        raise SessaoExpiradaError("Sessão do gov.br caiu durante o diagnóstico.")

    try:
        selecionou = selecionar_contribuinte(
            page, cliente,
            tentativas=TENTATIVAS_SELECAO_DIAGNOSTICO,
            timeout_nome_ms=TIMEOUT_NOME_DIAGNOSTICO_MS,
        )
    except SessaoExpiradaError:
        raise
    except Exception as erro:
        return {"perfil": "ERRO", "detalhe": f"falha inesperada na seleção: {erro}"}

    if not selecionou:
        return {
            "perfil": "SEM ACESSO",
            "detalhe": (
                "não foi possível selecionar o contribuinte — a empresa não "
                "apareceu/confirmou na lista. Verifique se há PROCURAÇÃO "
                "eletrônica para o escritório nesta empresa no portal."
            ),
        }

    ok_abas, detalhe_abas = _testar_abas_acessiveis(page)
    perfil = "OK" if ok_abas else "ERRO NA ABA"

    # Volta para a tela inicial para a próxima empresa (não é fatal se falhar).
    try:
        voltar_pagina_inicial(page, url_inicial)
    except Exception:
        logger.warning("Não consegui voltar à tela inicial após %s no diagnóstico.", cliente.nome_exibicao)

    return {"perfil": perfil, "detalhe": detalhe_abas}


def salvar_relatorio_teste_geral(linhas: list[dict]) -> Path:
    """Escreve o relatório TXT do diagnóstico, agrupado e com resumo no topo."""
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = Path(f"teste_geral_{marca}.txt")

    from collections import Counter
    contagem = Counter(l["perfil"] for l in linhas)
    ordem_perfis = ["OK", "SEM ACESSO", "ERRO NA ABA", "ERRO", "NÃO TESTADO"]

    largura = 74
    with open(caminho, "w", encoding="utf-8") as arq:
        arq.write("=" * largura + "\n")
        arq.write("  RELATÓRIO DE DIAGNÓSTICO — ROBÔ ISS DIGITAL LONDRINA\n")
        arq.write(f"  Gerado em: {datetime.now():%d/%m/%Y %H:%M:%S}\n")
        arq.write("  (Este teste NÃO baixou XML e NÃO alterou a planilha.)\n")
        arq.write("=" * largura + "\n\n")

        arq.write("RESUMO\n")
        arq.write("-" * largura + "\n")
        arq.write(f"  Total de empresas testadas: {len(linhas)}\n")
        for perfil in ordem_perfis:
            if contagem.get(perfil):
                arq.write(f"    {perfil:<14}: {contagem[perfil]}\n")
        arq.write("\n")
        arq.write("  Legenda:\n")
        arq.write("    OK          = seleciona e as abas Emitidas/Recebidas abrem\n")
        arq.write("    SEM ACESSO  = não apareceu na lista (verifique PROCURAÇÃO)\n")
        arq.write("    ERRO NA ABA = seleciona, mas uma aba travou (glitch do portal)\n")
        arq.write("    ERRO        = falha inesperada\n")
        arq.write("    NÃO TESTADO = a sessão caiu antes de chegar nesta empresa\n\n")

        # Detalhe agrupado por perfil, começando pelos que exigem sua atenção.
        for perfil in ["SEM ACESSO", "ERRO NA ABA", "ERRO", "NÃO TESTADO", "OK"]:
            grupo = [l for l in linhas if l["perfil"] == perfil]
            if not grupo:
                continue
            arq.write("=" * largura + "\n")
            arq.write(f"  {perfil}  ({len(grupo)})\n")
            arq.write("=" * largura + "\n")
            for l in grupo:
                arq.write(f"  [{l['codigo']}] {l['nome']}  (CNPJ {formatar_cnpj(l['cnpj'])})\n")
                arq.write(f"        → {l['detalhe']}\n")
            arq.write("\n")

    return caminho


def executar_teste_geral(clientes: list[ClienteLondrina]) -> int:
    """
    Passa por cada empresa só para descobrir o perfil (acessível? sem
    procuração? trava numa aba?). Não baixa XML, não toca na planilha de
    controle. Gera um relatório TXT ao final. Se a sessão do gov.br cair,
    para na hora e marca o restante como NÃO TESTADO.
    """
    # Filtra CNPJs inválidos antes (mesma regra do modo normal).
    linhas: list[dict] = []
    clientes_validos: list[ClienteLondrina] = []
    for cliente in clientes:
        if cnpj_valido(cliente.cnpj):
            clientes_validos.append(cliente)
        else:
            linhas.append({
                "codigo": cliente.codigo, "nome": cliente.nome_exibicao,
                "cnpj": cliente.cnpj, "perfil": "ERRO",
                "detalhe": "CNPJ com dígito verificador inválido no CSV — corrija o cadastro.",
            })

    with sync_playwright() as playwright:
        try:
            context, page = conectar_navegador_ja_logado(playwright)
        except Exception as erro:
            logger.error("Não foi possível conectar ao navegador: %s", erro)
            return 1

        if CAMINHO_PAGINA_INICIAL_ISS not in page.url:
            logger.error(
                "O diagnóstico deve começar na tela inicial de contribuintes "
                "do ISS Digital. URL atual: %s", page.url,
            )
            return 1
        url_inicial = page.url
        page.wait_for_timeout(2000)

        logger.info(
            "MODO DIAGNÓSTICO: testando %d empresa(s) SEM baixar nada.",
            len(clientes_validos),
        )

        sessao_caiu = False
        for i, cliente in enumerate(clientes_validos):
            logger.info(
                "Diagnóstico %d/%d: %s",
                i + 1, len(clientes_validos), cliente.nome_exibicao,
            )
            try:
                resultado = diagnosticar_cliente(page, cliente, url_inicial)
            except SessaoExpiradaError as erro:
                logger.error(
                    "%s\nSessão caiu no diagnóstico — parando. As empresas "
                    "restantes ficam como NÃO TESTADO. Relogue e rode de novo.",
                    erro,
                )
                sessao_caiu = True
                for restante in clientes_validos[i:]:
                    linhas.append({
                        "codigo": restante.codigo, "nome": restante.nome_exibicao,
                        "cnpj": restante.cnpj, "perfil": "NÃO TESTADO",
                        "detalhe": "a sessão do gov.br caiu antes de chegar nesta empresa.",
                    })
                break

            linhas.append({
                "codigo": cliente.codigo, "nome": cliente.nome_exibicao,
                "cnpj": cliente.cnpj, "perfil": resultado["perfil"],
                "detalhe": resultado["detalhe"],
            })
            # Pausa leve entre empresas (mais curta que o modo normal, já que
            # não há download).
            if i < len(clientes_validos) - 1 and not sessao_caiu:
                page.wait_for_timeout(1500)

        logger.info("Diagnóstico concluído. O navegador NÃO será fechado.")

    caminho_txt = salvar_relatorio_teste_geral(linhas)
    logger.info("Relatório de diagnóstico salvo em: %s", caminho_txt.resolve())

    from collections import Counter
    contagem = Counter(l["perfil"] for l in linhas)
    print("\n" + "=" * 60)
    print("DIAGNÓSTICO — RESUMO")
    print("=" * 60)
    for perfil, qtd in contagem.most_common():
        print(f"  {perfil:<14}: {qtd}")
    print(f"\n  Relatório completo: {caminho_txt.resolve()}")
    print("=" * 60 + "\n")

    # Retorna 0 se todas OK; 1 se houver qualquer perfil que peça atenção.
    problemas = sum(qtd for perfil, qtd in contagem.items() if perfil != "OK")
    return 1 if problemas else 0


# ═══════════════════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════════════════

def separar_codigos(texto: str) -> list[str]:
    """
    Aceita uma lista de códigos separados por qualquer coisa que não seja
    número: ponto, vírgula, espaço, ponto-e-vírgula. Ex.:
        "545.582.548.547"  -> ['545', '582', '548', '547']
        "545, 582; 548"    -> ['545', '582', '548']
    Mantém a ordem e remove repetidos, sem bagunçar.
    """
    achados = re.findall(r"\d+", texto or "")
    vistos: list[str] = []
    for codigo in achados:
        if codigo not in vistos:
            vistos.append(codigo)
    return vistos


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Robô ISS Digital Londrina")
    parser.add_argument(
        "--codigo-cliente",
        help=(
            "Processa somente um código de cliente. Use este modo para testar "
            "antes de executar a relação completa de clientes."
        ),
    )
    parser.add_argument(
        "--codigos",
        metavar='"545.582.548"',
        help=(
            "Processa somente os códigos informados, separados por ponto "
            "(ou vírgula/espaço). Ex.: --codigos \"545.582.548.547\" roda só "
            "essas quatro empresas. Ideal para baixar um punhado específico "
            "sem rodar a lista inteira."
        ),
    )
    parser.add_argument(
        "--teste-geral",
        action="store_true",
        help=(
            "MODO DIAGNÓSTICO: passa por TODAS as empresas da planilha (ou "
            "pelas de --codigos) apenas para descobrir o PERFIL de cada uma — "
            "se dá para selecionar, se abre as abas, se parece faltar "
            "procuração etc. NÃO baixa nenhum XML e NÃO mexe na planilha de "
            "controle. Gera um relatório TXT ao final."
        ),
    )
    parser.add_argument(
        "--retomar-pendencias",
        metavar="ARQUIVO.csv",
        help=(
            "Processa somente os clientes listados nesse CSV de pendências "
            "(gerado automaticamente ao final de cada execução, em "
            "pendencias_{competencia}.csv). Use para reprocessar só quem "
            "falhou, sem rodar todos novamente."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argumentos = criar_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    if sync_playwright is None:
        logger.error("Playwright não instalado. Execute INSTALAR.bat antes de usar o robô.")
        return 1

    problemas = validar_config()
    if problemas:
        for p in problemas:
            logger.error(p)
        return 1

    for aviso in obter_avisos_config():
        logger.warning("⚠ %s", aviso)

    # ── Validação dos filtros de seleção de empresas (05/08/2026) ──────────
    # --codigo-cliente, --codigos e --retomar-pendencias escolhem QUAIS
    # empresas rodar; usar mais de um ao mesmo tempo é ambíguo.
    modos_selecao = sum([
        bool(argumentos.codigo_cliente),
        bool(argumentos.codigos),
        bool(argumentos.retomar_pendencias),
    ])
    if modos_selecao > 1:
        logger.error(
            "Escolha apenas UM entre --codigo-cliente, --codigos e "
            "--retomar-pendencias."
        )
        return 1
    if argumentos.teste_geral and argumentos.retomar_pendencias:
        logger.error("--teste-geral não combina com --retomar-pendencias.")
        return 1

    # Carrega a lista completa uma vez. `todos_clientes` é sempre a lista
    # inteira (a planilha de controle precisa dela, mesmo quando só um punhado
    # de empresas será processado).
    todos_clientes = carregar_clientes_londrina(config.caminho_csv_clientes_londrina)
    clientes = list(todos_clientes)

    # ── Filtro por códigos específicos (vale para diagnóstico E download) ──
    if argumentos.codigos:
        codigos_alvo = separar_codigos(argumentos.codigos)
        if not codigos_alvo:
            logger.error("Nenhum código válido encontrado em --codigos: %r", argumentos.codigos)
            return 1
        por_codigo = {c.codigo: c for c in todos_clientes}
        clientes = [por_codigo[c] for c in codigos_alvo if c in por_codigo]
        faltando = [c for c in codigos_alvo if c not in por_codigo]
        if faltando:
            logger.warning(
                "Código(s) não encontrado(s) no CSV e ignorado(s): %s",
                ", ".join(faltando),
            )
        if not clientes:
            logger.error("Nenhum dos códigos informados existe no CSV de clientes.")
            return 1
        logger.info(
            "MODO SELEÇÃO: %d empresa(s) escolhida(s) por código (%s).",
            len(clientes), ", ".join(c.codigo for c in clientes),
        )
    elif argumentos.codigo_cliente:
        clientes = [c for c in todos_clientes if c.codigo == argumentos.codigo_cliente.strip()]
        if not clientes:
            logger.error("Código de cliente %s não existe no CSV.", argumentos.codigo_cliente)
            return 1
        logger.info(
            "MODO DE TESTE: somente o cliente de código %s será processado.",
            argumentos.codigo_cliente,
        )

    # ── MODO DIAGNÓSTICO: sai aqui, SEM pedir período e SEM tocar na planilha ──
    if argumentos.teste_geral:
        return executar_teste_geral(clientes)

    # ── MODOS DE DOWNLOAD daqui para baixo (precisam de período e planilha) ──
    data_inicial, data_final, competencia_mmaaaa = solicitar_periodo_ao_usuario()
    blocos = dividir_periodo_em_blocos(data_inicial, data_final)
    logger.info(
        "Período: %s a %s — competência de destino: %s (%d bloco(s))",
        data_inicial, data_final, competencia_mmaaaa, len(blocos),
    )

    raiz_relatorios = Path(config.caminho_raiz_relatorios_conferencia)
    try:
        caminho_controle = inicializar_controle(
            todos_clientes, competencia_mmaaaa, raiz_relatorios
        )
        logger.info("Planilha de controle: %s", caminho_controle)
    except Exception as erro:
        logger.error(
            "Não foi possível criar/abrir a planilha de controle: %s. "
            "Feche a planilha no Excel e tente novamente.", erro,
        )
        return 1

    if argumentos.retomar_pendencias:
        try:
            codigos_pendentes = carregar_codigos_pendentes(argumentos.retomar_pendencias)
        except FileNotFoundError as erro:
            logger.error(str(erro))
            return 1
        clientes = [c for c in todos_clientes if c.codigo in codigos_pendentes]
        codigos_nao_encontrados = codigos_pendentes - {c.codigo for c in clientes}
        if codigos_nao_encontrados:
            logger.warning(
                "%d código(s) do arquivo de pendências não existem mais no "
                "CSV de clientes: %s",
                len(codigos_nao_encontrados), ", ".join(sorted(codigos_nao_encontrados)),
            )
        if not clientes:
            logger.error("Nenhum dos códigos pendentes foi encontrado no CSV de clientes.")
            return 1
        logger.info(
            "MODO RETOMADA: processando apenas os %d cliente(s) pendentes de %s.",
            len(clientes), argumentos.retomar_pendencias,
        )
    clientes_por_codigo = {cliente.codigo: cliente for cliente in todos_clientes}
    pastas_clientes_emitidas = descobrir_pastas_de_cliente_dominio(
        Path(config.caminho_raiz_importacao_dominio), clientes_por_codigo
    )
    pastas_clientes_tomados = descobrir_pastas_de_cliente_dominio(
        Path(config.caminho_raiz_importacao_tomados), clientes_por_codigo
    )
    pastas_clientes_relatorios = descobrir_pastas_de_cliente_dominio(
        raiz_relatorios, clientes_por_codigo
    )

    # Validação de CNPJ adicionada após auditoria externa (28/07/2026) —
    # clientes com dígito verificador inválido nunca chegam a tentar o
    # portal; entram direto no relatório como pendência.
    resultados = []
    clientes_validos = []
    for cliente in clientes:
        if cnpj_valido(cliente.cnpj):
            clientes_validos.append(cliente)
        else:
            logger.warning(
                "CNPJ inválido (dígito verificador) — código %s (%s): %s. "
                "Não será processado.",
                cliente.codigo, cliente.apelido or "sem apelido", cliente.cnpj,
            )
            resultado_invalido = ResultadoCliente(
                codigo_cliente=cliente.codigo, nome=cliente.nome_exibicao,
                cnpj=cliente.cnpj,
                status="pendencia_cnpj_invalido",
                detalhe=f"CNPJ {formatar_cnpj(cliente.cnpj)} com dígito verificador inválido",
                emitidos="ERRO", recebidos="ERRO", relatorio="ERRO",
            )
            resultados.append(resultado_invalido)
            atualizar_controle(caminho_controle, competencia_mmaaaa, resultado_invalido)

    if len(clientes_validos) < len(clientes):
        logger.warning(
            "%d de %d clientes excluídos por CNPJ inválido — ver relatório final.",
            len(clientes) - len(clientes_validos), len(clientes),
        )
    clientes = clientes_validos

    with sync_playwright() as playwright:
        try:
            context, page = conectar_navegador_ja_logado(playwright)
        except Exception as erro:
            logger.error("Não foi possível conectar ao navegador: %s", erro)
            return 1

        logger.info("Conectado. Iniciando processamento de %d clientes.", len(clientes))

        # Guarda a URL completa da página inicial, inclusive o número da
        # sessão APEX. Ela será usada no retorno entre os clientes; clicar no
        # link genérico "Página Inicial" pode levar ao portal público.
        if CAMINHO_PAGINA_INICIAL_ISS not in page.url:
            logger.error(
                "A execução deve começar na tela inicial de contribuintes do "
                "ISS Digital. URL atual: %s",
                page.url,
            )
            return 1
        url_pagina_inicial_iss = page.url

        # Pausa de estabilização adicionada após log real (29/07/2026): os
        # dois primeiros clientes de uma execução falharam ao selecionar o
        # contribuinte, e o terceiro (logo depois) funcionou — hipótese é
        # que a página recém-conectada via CDP precisa de um instante para
        # sincronizar antes do primeiro uso confiável dos seletores.
        page.wait_for_timeout(2000)

        for i, cliente in enumerate(clientes):
            logger.info("Processando %d/%d: %s", i + 1, len(clientes), cliente.nome_exibicao)
            try:
                resultado = processar_cliente(
                    page, cliente, blocos, competencia_mmaaaa,
                    pastas_clientes_emitidas, pastas_clientes_tomados,
                    pastas_clientes_relatorios, raiz_relatorios,
                    url_pagina_inicial_iss,
                )
            except SessaoExpiradaError as erro:
                # A sessão do gov.br caiu no meio da rodada. Era ISSO que
                # transformava 1 queda em centenas de "falhas" (cada empresa
                # seguinte esperava minutos na tela de login e falhava). Agora
                # o robô PARA na hora. IMPORTANTE: os clientes que faltaram NÃO
                # recebem "ERRO" na planilha — ficam como estavam (Aguardando),
                # e entram só no CSV de pendências, para você relogar e retomar
                # só eles. Assim a planilha não fica poluída de ERRO à toa.
                nao_processados = clientes[i:]
                logger.error(
                    "%s\n"
                    "════════════════════════════════════════════════════════\n"
                    "  A SESSÃO DO GOV.BR CAIU — execução PARADA aqui.\n"
                    "  %d cliente(s) ainda não processados NÃO foram marcados\n"
                    "  como erro (ficaram 'Aguardando' na planilha).\n"
                    "  → Reabra/relogue o Chrome (ver README) e rode de novo\n"
                    "    com RETOMAR_PENDENCIAS.bat para pegar só o que faltou.\n"
                    "════════════════════════════════════════════════════════",
                    erro, len(nao_processados),
                )
                for cliente_restante in nao_processados:
                    # Registrado apenas para o CSV de pendências (retomada) —
                    # NÃO vai para a planilha como ERRO.
                    resultados.append(ResultadoCliente(
                        codigo_cliente=cliente_restante.codigo,
                        nome=cliente_restante.nome_exibicao,
                        cnpj=cliente_restante.cnpj,
                        status="nao_processado_sessao_expirou",
                        detalhe="Não processado: a sessão do gov.br caiu antes de chegar neste cliente",
                        emitidos="", recebidos="", relatorio="",
                    ))
                imprimir_relatorio(resultados, competencia_mmaaaa)
                return 1
            except NavegadorFechadoError as erro:
                logger.error(
                    "%s\nPARANDO A EXECUÇÃO — não adianta tentar os %d "
                    "clientes restantes sem navegador. Reabra o Chrome "
                    "(ver README), faça login, e rode o robô de novo.",
                    erro, len(clientes) - i,
                )
                for cliente_restante in clientes[i:]:
                    resultado_interrompido = ResultadoCliente(
                        codigo_cliente=cliente_restante.codigo,
                        nome=cliente_restante.nome_exibicao,
                        cnpj=cliente_restante.cnpj,
                        status="falha",
                        detalhe="Não processado: navegador foi fechado durante a execução",
                        emitidos="", recebidos="", relatorio="",
                    )
                    resultados.append(resultado_interrompido)
                imprimir_relatorio(resultados, competencia_mmaaaa)
                return 1

            resultados.append(resultado)
            try:
                atualizar_controle(caminho_controle, competencia_mmaaaa, resultado)
            except PermissionError:
                logger.error(
                    "A planilha de controle está aberta no Excel. Feche-a e "
                    "reprocesse este cliente pela lista de pendências."
                )
                resultado = ResultadoCliente(
                    codigo_cliente=resultado.codigo_cliente,
                    nome=resultado.nome,
                    cnpj=resultado.cnpj,
                    status="falha",
                    detalhe=(resultado.detalhe or "") + "; planilha de controle bloqueada pelo Excel",
                    emitidos=resultado.emitidos,
                    recebidos=resultado.recebidos,
                    relatorio="ERRO",
                    xml_emitidos=resultado.xml_emitidos,
                    xml_recebidos=resultado.xml_recebidos,
                )
                resultados[-1] = resultado
            if i < len(clientes) - 1:
                time.sleep(PAUSA_ENTRE_CLIENTES_SEGUNDOS)

        logger.info(
            "Processamento concluído. O navegador NÃO será fechado "
            "automaticamente — é a sua janela, feche quando quiser."
        )

    imprimir_relatorio(resultados, competencia_mmaaaa)

    # Corrigido após auditoria externa (30/07/2026, achado A-01): antes,
    # o robô retornava 0 (sucesso) mesmo com 100% dos clientes em falha —
    # o que faria um agendador ou operador acreditar que deu tudo certo.
    total_falhas = sum(1 for r in resultados if r.status != "processado")

    if total_falhas:
        logger.error(
            "Execução terminou com %d pendência(s) técnica(s), de %d cliente(s). "
            "Use RETOMAR_PENDENCIAS.bat depois de corrigir a causa.",
            total_falhas, len(resultados),
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
