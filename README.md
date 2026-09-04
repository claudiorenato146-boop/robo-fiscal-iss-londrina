# Robô Fiscal 2.0 — ISS Digital Londrina

Automatiza no portal do ISS Digital de Londrina o que era feito empresa por
empresa, à mão: selecionar o contribuinte, baixar as NFS-e emitidas e recebidas,
baixar o relatório de conferência e anotar o resultado numa planilha mensal.

O cadastro de clientes fica **fora do repositório**. Copie
`clientes_londrina.exemplo.csv` para `clientes_londrina.csv` e preencha com as
suas empresas — o `.gitignore` já impede que o arquivo real seja versionado.

## O que o robô faz

1. Conecta ao Chrome especial já aberto e logado no ISS Digital.
2. Seleciona cada contribuinte pelo CNPJ.
3. Baixa e salva o ZIP de NFS-e emitidas em `Importação`.
4. Baixa e salva o ZIP de NFS-e recebidas em `Importação - Tomados`.
5. Baixa os relatórios Excel disponíveis em `Relatorios Conferencia ISS`.
6. Abre cada ZIP e conta somente arquivos `.xml`:
   - `OK`: existe pelo menos um XML dentro do ZIP;
   - `X`: ZIP válido sem XML, portanto sem movimento;
   - `ERRO`: falha técnica, arquivo ausente/corrompido ou pasta inexistente.
7. Atualiza a planilha única `PLANILHA RELATORIO MENSAL.xlsx` depois de cada empresa.
8. Cria ou reutiliza uma aba com o nome da competência (`072026`, `082026` etc.), sem apagar meses anteriores.
9. Gera `pendencias_MMAAAA.csv` somente para falhas técnicas.
10. Aguarda o processamento do portal em cada etapa: até 2 minutos para
    telas, campos e botões, e até 5 minutos para downloads, antes de registrar
    uma falha.

Um ZIP válido sem XML **não é pendência**. Se emitidas estiver vazio e recebidas tiver XML, o resultado correto é `X / OK`.

Na planilha, `XML - PRESTADOS` corresponde às NFS-e emitidas e `XML - TOMADOS` corresponde às NFS-e recebidas. Falhas do relatório Excel ficam descritas em `OBSERVAÇÃO` e entram nas pendências técnicas.

## Primeira instalação em cada computador

Abra o CMD dentro desta pasta e execute:

```bat
INSTALAR.bat
```

## Execução normal

1. Execute `python abrir_chrome_iss.py` (ou `INICIAR_CHROME_ISS.bat`).
2. Faça o login manual com certificado e captcha.
3. Deixe o ISS Digital na tela inicial de contribuintes.
4. Volte ao CMD e execute:

```bat
EXECUTAR_ROBO.bat
```

Informe a competência quando solicitado, por exemplo `07.2026`.

## Testar uma única empresa

Com o Chrome especial aberto e logado:

```bat
TESTAR_UMA_EMPRESA.bat
```

Digite o código mostrado na primeira coluna de `clientes_londrina.csv`.

## Rodar só algumas empresas escolhidas (novo)

Com o Chrome especial aberto e logado:

```bat
RODAR_EMPRESAS_SELECIONADAS.bat
```

Informe os códigos separados por **ponto**, por exemplo `545.582.548.547` (também aceita vírgula ou espaço). Só essas empresas serão baixadas. Pela linha de comando é o mesmo que:

```bat
python robo_londrina.py --codigos "545.582.548.547"
```

## Teste geral / diagnóstico — sem baixar XML (novo)

Passa por **todas** as empresas só para descobrir o **perfil** de cada uma (se seleciona, se as abas abrem, se parece faltar procuração). **Não** baixa nenhum XML e **não** altera a planilha de controle. Ao final gera um relatório TXT nesta pasta (`teste_geral_AAAAMMDD_HHMMSS.txt`).

```bat
TESTE_GERAL.bat
```

Perfis do relatório: `OK` (seleciona e abas abrem), `SEM ACESSO` (não apareceu na lista — verifique a procuração no portal), `ERRO NA ABA` (seleciona mas uma aba travou), `ERRO` (falha inesperada), `NÃO TESTADO` (a sessão caiu antes de chegar nessa empresa). Também funciona para um subconjunto: `python robo_londrina.py --teste-geral --codigos "545.582.548"`.

## Reprocessar somente as pendências

```bat
RETOMAR_PENDENCIAS.bat
```

Informe a competência no formato `MMAAAA`, por exemplo `072026`. O robô usa o arquivo `pendencias_072026.csv`.

> **Se a sessão do gov.br cair no meio da rodada**, o robô agora para na hora (em vez de falhar todas as empresas seguintes). As que faltaram **não** recebem `ERRO` na planilha — ficam `Aguardando`. Basta relogar no Chrome e usar `RETOMAR_PENDENCIAS.bat`.

## Reconferir os arquivos no servidor

Esta opção não acessa o portal. Ela abre os ZIPs que já estão nas pastas, atualiza a planilha de controle e reconstrói a lista de pendências:

```bat
VERIFICAR_ARQUIVOS.bat
```

## Cuidados importantes

- A planilha `PLANILHA RELATORIO MENSAL.xlsx` fica solta na raiz de `Relatorios Conferencia ISS`.
- Todo mês o robô cria ou reutiliza a aba `MMAAAA` informada, preservando todas as demais abas.
- Não deixe essa planilha aberta no Excel enquanto o robô estiver executando.
- O robô cria somente a subpasta da competência. Ele nunca cria silenciosamente uma pasta de cliente nas raízes de importação.
- Na pasta de relatórios, a pasta do cliente pode ser criada automaticamente.
- Se o navegador fechar, os clientes restantes são registrados como pendentes.
- Os arquivos já existentes não são sobrescritos: conteúdo idêntico é reaproveitado; conteúdo diferente recebe versão `__v2`, `__v3` e assim por diante.
- Prints de falhas ficam na pasta `diagnosticos_falha`.

---

## Testes

```bash
pip install pytest
pytest -q
```

27 testes, sem tocar no portal do ISS: cobrem a leitura do cadastro (incluindo
CSV com BOM e CNPJ mascarado), a validação de CNPJ pelo dígito verificador, a
classificação `OK` / `X` / `ERRO` e a escrita na planilha mensal — uma aba por
competência, sem apagar as anteriores.

O `PLANILHA RELATORIO MENSAL.xlsx` versionado é um **modelo em branco**: só
cabeçalho e formatação. O robô o copia para a pasta de relatórios na primeira
execução e a partir daí trabalha na cópia.

## Segurança

- O `.env` **não é versionado**. Copie o `.env.example` e preencha na máquina.
- `SENHA_CERTIFICADO_ESCRITORIO` só é usada para avisar do vencimento do
  certificado; a autenticação no portal é manual, no seu próprio Chrome.
- **Não coloque a senha no nome do arquivo `.pfx`** — ela aparece em qualquer
  listagem de pasta, backup ou captura de tela.
- O cadastro real, as pendências, a planilha preenchida e os prints de falha
  ficam todos no `.gitignore`. Confira com `git status` antes de cada commit.

## Licença

MIT — veja [LICENSE](LICENSE). Use, copie e adapte à vontade.

## Aviso

Este repositório traz **só o código**. Nenhum cadastro de cliente, nenhuma
credencial e nenhum certificado estão aqui, e os caminhos de rede nos exemplos
são genéricos. Os arquivos `*.exemplo.*` existem para o projeto rodar sem
depender de dado real — copie, renomeie e preencha com os seus.
