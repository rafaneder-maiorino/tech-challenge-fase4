# Notas para Windows — riscos, correções e o resultado do teste

Levantamento feito no Mac em 2026-10-12, lendo o `README.md` e cada alvo do
`Makefile`. Os itens **certos** de quebrar foram corrigidos no Mac antes do
teste (branch `fix/windows-portability`, PR #13); o teste numa máquina Windows
x64 confirmou ou refutou cada um, e os defeitos que ele achou foram corrigidos
na branch `fix/windows-findings`.

**Resultado geral:** tudo o que é estrutural passou — `make all` com todos os
testes, `make alerts-test` via Docker, os dois `validate-*` com os códigos de
saída certos, a stack inteira, as três dashboards, e os *lead times* -1 e -2
idênticos aos do macOS. Quatro defeitos, nenhum bloqueante (W12, W14, W17,
W18), e uma diferença que não tem conserto e não muda conclusão (W13).

Na coluna "status", **passou** e **confirmado** são resultados reportados do
teste; **implícito** é o que decorre de um resultado reportado sem ter sido
checado isoladamente; **não reportado** e **não exercitado** dizem o que não se
sabe.

## A variável que decide quase tudo: qual shell o `make` usa

O GNU make para Windows roda as receitas com `sh.exe` **se encontrar um no
PATH**, e com `cmd.exe` se não encontrar. O Git Bash **não traz `make`**: nos
dois casos ele precisa ser instalado (`winget install ezwinports.make`,
`choco install make` ou `scoop install make`).

| item | status |
|---|---|
| `$(info)` + `@exit 0` no GNU make 4.4 (só tinha sido testado no make 3.81 do macOS): mensagens na ordem certa, sem "is up to date" | **passou** |

## Corrigidos no Mac antes do teste

| # | alvo | o que assumia Unix | correção | status |
|---|---|---|---|---|
| W1 | todos (pré-requisito) | `make` instalado; o README não falava de Windows | seção **Windows** no README | **implícito** — o `make all` rodou seguindo o README |
| W2 | todos, na leitura do Makefile | `BATCH_STAMP := $(shell date +%Y%m%d)`; no `cmd`, `date` tenta mudar a data do sistema | removido; o nome do lote vem do código que o escreve | **implícito** — o `make all` terminou, sem travar |
| W3 | `validate-bad-batch`, `validate-clean-batch` | receita POSIX `status=$$? ; exit $$status` | `scripts/make_dirty_batch.py --validate` | **passou** — bad != 0, clean 0 |
| W4 | `monitor-all` | dependia de `data/dirty/`, que só `validate-bad-batch` produzia (achado §16) | `ensure_dirty_batch`; `make dirty-batch` no `make all` | **implícito** — a stack completa e os *lead times* vieram do `monitor-all` |
| W5 | `alerts-test` | download do promtool com `uname`, `sed`, `tr`, `curl \| tar`, `chmod` | promtool da imagem fixada, via Docker (`scripts/alerts_test.py`) | **passou** |
| W6 | `alerts-test` | `.tools/promtool` como comando com barra, glob sem expansão | resolvido junto com W5 | **passou** (junto com W5) |
| W7 | `help`, `all`, `stack-up` | `@echo "..."` | `$(info ...)` + alvos auxiliares | **passou** |
| W8 | `all`, mensagem final | `make stack-up && make monitor-all` | uma linha por comando | **não reportado** |
| W9 | `monitor-replay` | `PAUSE=5 make ...` | README documenta `make monitor-replay PAUSE=5` | **não reportado** |
| W11 | `docker compose down -v` | sem `-f` | README: `docker compose -f docker-compose.monitoring.yml down -v` | **não reportado** |

## Hipóteses — o que o teste respondeu

| # | onde | hipótese | status |
|---|---|---|---|
| W10 | Alloy lendo `./logs` pelo bind mount do Docker Desktop | atraso, não falha, por falta de inotify | **não exercitado** |
| W12 | 18 escritas de texto sem `newline=` | o Windows grava CRLF | **confirmado** — oito arquivos versionados modificados depois do `make all`. **Refutada** a outra metade da hipótese: o `git status` *não* escondeu o CRLF; marcou os arquivos como modificados (com `git diff` vazio). Corrigido: `newline="\n"` em toda escrita de texto — as 17 com `write_text`, a ingestão, o HTML do Evidently (que abria o arquivo em modo texto por conta própria) e o handler dos logs JSONL. `tests/test_portability.py` falha em qualquer escrita nova sem `newline=` |
| W13 | números do XGBoost | não bit a bit idênticos entre plataformas | **confirmado** — AUC 0,8561 (macOS ARM) contra 0,8553 (Windows x64), mesma semente; `OMP_NUM_THREADS=1` deu um terceiro valor (0,8557), o que descarta a ordem das reduções paralelas e aponta para o build. A regressão logística sai idêntica. **Não tem conserto e não muda conclusão**: delta XGBoost − logística +0,0072 com 5/5 vitórias no Windows, +0,0071 e 5/5 no macOS; a diferença entre plataformas é uma ordem de grandeza menor que o ruído entre sementes. Achado §17 |
| W14 | acentos no console | `UnicodeEncodeError` com saída redirecionada | **confirmado, por outro caminho** — o Python ficou certo (`PYTHONUTF8=1`); quem quebrou foi o próprio `make`: `make stack-up` imprimiu `an├┤nimo`, os bytes UTF-8 do Makefile lidos em cp850. Corrigido: o Makefile roda `chcp.com 65001` ao começar, no Windows. **A confirmar** no próximo teste no Windows |
| W15 | `make train` | o *wheel* do XGBoost pode exigir o VC++ Redistributable | **passou** |
| W16 | caminhos longos | `.venv/` e `mlruns/` passam de 260 caracteres | **não exercitado** |

## Achados no teste que não estavam na lista

| # | onde | defeito | correção | status |
|---|---|---|---|---|
| W17 | conteúdo dos relatórios | caminhos com `\` dentro do texto: `reports/inspection.md` dizia `data\raw\gmsc.parquet` | `credit_monitor.paths.display_path` (relativo ao repositório, sempre `/`) em todo caminho escrito em relatório, JSON, log e saída de console; um filtro de logging cobre os `log.info("... %s", path)`; `json_default` cobre os JSON | corrigido no Mac — **a confirmar** |
| W18 | dashboard `cm-feature` | a variável `feature` abria vazia: "No data" nos cinco painéis até escolher uma à mão | a variável era uma consulta `label_values(drift_psi, feature)` rodada só ao carregar a dashboard; aberta antes do `monitor-all`, voltava vazia e descartava o padrão. Agora é uma lista fixa das features do modelo, com padrão `NumberOfTimes90DaysLate` (maior participação no ganho, 28,47%) | corrigido no Mac — **a confirmar** |

### O que **não** é risco (verificado no Mac)

- **`rm -rf`, `export VAR=`, `source .venv/...`**: não aparecem no Makefile nem
  no README.
- **`uvloop`** aparece no `uv sync` do Mac, mas no `uv.lock` está marcado
  `sys_platform != 'win32'`.
- **Fim de linha no checkout**: o `.gitattributes` força LF em Makefile, YAML,
  JSON, Python e MD, qualquer que seja o `core.autocrlf`.

## Para o próximo teste no Windows

1. `make all` em diretório novo; a checagem byte a byte do README. Esperado:
   `reports/inspection.md` e os três JSON das dashboards **idênticos** (W12,
   W17); os arquivos que passam pelo campeão podem diferir nas últimas casas
   (W13).
2. `make stack-up`: os acentos do `make` certos no PowerShell (W14).
3. Abrir `cm-feature` **antes** do `make monitor-all`, depois rodar o
   `monitor-all` e recarregar: tem de mostrar `NumberOfTimes90DaysLate` com
   dado (W18).
4. Se houver tempo: W8, W9, W11, W10, W16.
