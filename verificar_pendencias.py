"""Reconstrói pendências conferindo os arquivos reais dentro das pastas.

Uso:
    python verificar_pendencias.py 072026

Regra principal:
    ZIP válido com XML    -> OK (há movimento)
    ZIP válido sem XML   -> X  (sem movimento, não é pendência)
    ZIP ausente/inválido -> ERRO (pendência técnica)
"""

from __future__ import annotations

import sys
from pathlib import Path

from config import config
from controle_resultados import atualizar_controle, inicializar_controle
from robo_londrina import (
    ResultadoCliente,
    avaliar_zips_existentes,
    carregar_clientes_londrina,
    descobrir_pastas_de_cliente_dominio,
    salvar_pendencias_csv,
)


def _localizar_zips(pasta: Path | None, padrao: str) -> list[Path]:
    if pasta is None or not pasta.exists():
        return []
    return sorted(pasta.glob(padrao))


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit() or len(sys.argv[1]) != 6:
        print("Uso: python verificar_pendencias.py MMAAAA  (ex.: 072026)")
        return 1
    competencia = sys.argv[1].strip()

    clientes = carregar_clientes_londrina(config.caminho_csv_clientes_londrina)
    pastas_emitidas = descobrir_pastas_de_cliente_dominio(
        Path(config.caminho_raiz_importacao_dominio)
    )
    pastas_tomados = descobrir_pastas_de_cliente_dominio(
        Path(config.caminho_raiz_importacao_tomados)
    )
    raiz_relatorios = Path(config.caminho_raiz_relatorios_conferencia)
    pastas_relatorios = descobrir_pastas_de_cliente_dominio(raiz_relatorios)
    caminho_controle = inicializar_controle(clientes, competencia, raiz_relatorios)

    resultados: list[ResultadoCliente] = []
    for cliente in clientes:
        erros: list[str] = []
        status_emitidos = "ERRO"
        status_recebidos = "ERRO"
        xml_emitidos = None
        xml_recebidos = None

        info_emitidas = pastas_emitidas.get(cliente.codigo)
        info_recebidas = pastas_tomados.get(cliente.codigo)
        if info_emitidas is None:
            erros.append("emitidas: pasta do cliente não encontrada")
        else:
            try:
                status_emitidos, xml_emitidos, _ = avaliar_zips_existentes(
                    _localizar_zips(
                        info_emitidas.caminho / competencia,
                        f"xml_nfse_emitidas_{cliente.cnpj}*.zip",
                    )
                )
            except Exception as erro:
                erros.append(f"emitidas: {erro}")

        if info_recebidas is None:
            erros.append("recebidas: pasta do cliente não encontrada")
        else:
            try:
                status_recebidos, xml_recebidos, _ = avaliar_zips_existentes(
                    _localizar_zips(
                        info_recebidas.caminho / competencia,
                        f"xml_nfse_recebidas_{cliente.cnpj}*.zip",
                    )
                )
            except Exception as erro:
                erros.append(f"recebidas: {erro}")

        # O relatório Excel é exigido quando houve movimento em pelo menos
        # um dos tipos. Sem movimento nos dois, ausência de relatório é X,
        # não pendência.
        info_relatorio = pastas_relatorios.get(cliente.codigo)
        pasta_relatorio = (
            info_relatorio.caminho / competencia
            if info_relatorio is not None
            else raiz_relatorios / f"{cliente.codigo}-{cliente.apelido}" / competencia
        )
        relatorios = list(pasta_relatorio.glob("*.xlsx")) if pasta_relatorio.exists() else []
        houve_movimento = status_emitidos == "OK" or status_recebidos == "OK"
        if relatorios:
            status_relatorio = "OK"
        elif houve_movimento:
            status_relatorio = "ERRO"
            erros.append("relatório: arquivo Excel não encontrado")
        else:
            status_relatorio = "X"

        detalhe = (
            f"Emitidos: {status_emitidos} ({xml_emitidos if xml_emitidos is not None else '-'} XML); "
            f"Recebidos: {status_recebidos} ({xml_recebidos if xml_recebidos is not None else '-'} XML); "
            f"Relatório: {status_relatorio}"
        )
        if erros:
            detalhe += "; " + "; ".join(erros)

        resultado = ResultadoCliente(
            codigo_cliente=cliente.codigo,
            nome=cliente.nome_exibicao,
            cnpj=cliente.cnpj,
            status="falha" if erros else "processado",
            detalhe=detalhe,
            emitidos=status_emitidos,
            recebidos=status_recebidos,
            relatorio=status_relatorio,
            xml_emitidos=xml_emitidos,
            xml_recebidos=xml_recebidos,
        )
        resultados.append(resultado)
        atualizar_controle(caminho_controle, competencia, resultado)

    pendentes = sum(1 for resultado in resultados if resultado.status != "processado")
    sem_movimento = sum(
        1
        for resultado in resultados
        if resultado.status == "processado"
        and resultado.emitidos == "X"
        and resultado.recebidos == "X"
    )
    print(f"\nTotal de clientes: {len(resultados)}")
    print(f"Sem movimento nos dois tipos (não pendentes): {sem_movimento}")
    print(f"Pendências técnicas: {pendentes}")
    print(f"Planilha atualizada: {caminho_controle.resolve()}")

    caminho_pendencias = salvar_pendencias_csv(resultados, competencia)
    if caminho_pendencias:
        print(f"Arquivo de pendências: {caminho_pendencias.resolve()}")
        print(
            "Para reprocessar somente elas: "
            f"python robo_londrina.py --retomar-pendencias {caminho_pendencias}"
        )
    else:
        print("Nenhuma pendência técnica encontrada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
