# Notas para Windows — riscos, correções e o que falta confirmar

Levantamento feito no Mac em 2026-10-12, lendo o `README.md` e cada alvo do
`Makefile` (a partir do commit `ba3b0e7`). Os itens **certos** de quebrar foram
corrigidos no Mac na branch `fix/windows-portability`, mantendo o macOS byte a
byte idêntico. Os demais são **hipóteses**: só uma máquina Windows os confirma
ou refuta, e a coluna "status" fica para ser preenchida lá.

## A variável que decide quase tudo: qual shell o `make` usa

O GNU make para Windows roda as receitas com `sh.exe` **se encontrar um no
PATH**, e com `cmd.exe` se não encontrar. A instalação padrão do Git for Windows
põe só `Git\cmd` no PATH, que não tem `sh.exe`. Então:

- **PowerShell ou cmd** → receitas rodam em `cmd.exe`. É o caso pior e o mais
  provável para um avaliador.
- **Git Bash** → receitas rodam em `sh`.

O Git Bash **não traz `make`**: nos dois casos ele precisa ser instalado
(`winget install ezwinports.make`, `choco install make` ou `scoop install make`).

Depois das correções, nenhum alvo do caminho do avaliador depende do shell. O
teste deve rodar nos dois e registrar se isso é verdade.

## Corrigidos no Mac — a confirmar no Windows

| # | alvo | o que assumia Unix | correção | status |
|---|---|---|---|---|
| W1 | todos (pré-requisito) | `make` instalado; o README não falava de Windows | seção **Windows** no README: make via winget/choco/scoop, Docker Desktop com WSL2, VC++ Redistributable, `core.longpaths` | corrigido no Mac — confirmar |
| W2 | todos, na leitura do Makefile | `BATCH_STAMP := $(shell date +%Y%m%d)`, avaliado a cada `make`, inclusive nas sub-makes de `make all`; no `cmd`, `date` tenta **mudar** a data do sistema e pode esperar entrada | **removido**. O nome do lote é dado pelo código que o escreve (`write_demo_batch`), e a ingestão recebe esse caminho. Sem consumidor, não havia o que tornar preguiçoso | corrigido no Mac — confirmar |
| W3 | `validate-bad-batch`, `validate-clean-batch` | receita POSIX `status=$$? ; ... ; exit $$status` | `scripts/make_dirty_batch.py --validate`: gera, roda a ingestão num subprocesso e sai com o código dela. O bad continua saindo != 0 (make: `Error 1`) | corrigido no Mac — confirmar |
| W4 | `monitor-all`, `monitor-replay` | dependiam de `data/dirty/`, que só `validate-bad-batch` produzia — quebrado também no macOS (achado §16) | `monitor-all` gera o lote se faltar (`ensure_dirty_batch`); `make all` ganhou `make dirty-batch`, que chama a mesma função | corrigido no Mac — confirmar |
| W5 | `$(PROMTOOL)` → `alerts-test` | `mkdir -p`, `uname \| sed '...'`, `tr`, `curl \| tar -C /tmp`, `chmod`; errado até no Git Bash (`MINGW64_NT`, release `.zip`) | `scripts/alerts_test.py`: promtool de dentro de `prom/prometheus:v3.13.3`, imagem lida do próprio compose. Sem Docker, aceita um `.tools/promtool[.exe]` **se** reportar a mesma versão. O download virou `make promtool`, explícito e só para Linux/macOS | corrigido no Mac — confirmar |
| W6 | `alerts-test` | `.tools/promtool` como comando com barra, glob `*.rules.yml`, `cd ... && ../../` | resolvido junto com W5: o glob é feito em Python | corrigido no Mac — confirmar |
| W7 | `help`, `all`, `stack-up` | `@echo "..."` — o `cmd` imprime as aspas e `""` no lugar da linha em branco | `$(info ...)` + `@exit 0`. As mensagens que têm de sair **depois** de um comando (fim do `all`, endereços do `stack-up`) vêm de alvos auxiliares (`all-done`, `stack-urls`), porque o make expande a receita inteira antes de rodá-la | corrigido no Mac — confirmar |
| W8 | `all`, mensagem final | sugeria `make stack-up && make monitor-all`; `&&` não existe no PowerShell 5.1 | uma linha por comando | corrigido no Mac — confirmar |
| W9 | `monitor-replay` | o hábito `PAUSE=5 make monitor-replay` | README documenta `make monitor-replay PAUSE=5` | corrigido no Mac — confirmar |
| W11 | `docker compose down -v` (README) | nome de arquivo padrão — falhou no Mac também | README: `docker compose -f docker-compose.monitoring.yml down -v` | corrigido no Mac — confirmar |
| W14 | `bias_analysis.py:196` imprime `→` | fora do cp1252: com saída redirecionada, `UnicodeEncodeError` | `export PYTHONUTF8 := 1` no Makefile (diretiva do make, vale em sh e cmd). No macOS, sem efeito nos bytes | corrigido no Mac — confirmar |

Para o teste no Windows, "confirmar" quer dizer: o alvo roda no PowerShell
**sem** Git Bash no PATH, e no Git Bash, com a mesma saída.

## Hipóteses — só o Windows responde

| # | onde | hipótese | como medir | status |
|---|---|---|---|---|
| W10 | `stack-up` → Alloy | sob Docker Desktop (WSL2), eventos de inotify não cruzam do NTFS para a VM. O Alloy faz polling, então o esperado é **atraso**, não falha | depois do `monitor-all`, contar no Loki: no Mac deram **93 streams / 183 linhas** | |
| W12 | 18 chamadas `write_text(...)` / `open("w")` sem `newline=` | o Python no Windows grava `\r\n`; os relatórios versionados sairiam em CRLF, e o `git status` provavelmente continuaria limpo porque o `eol=lf` normaliza na comparação | a checagem `git hash-object --no-filters` do README (no Mac: 20 arquivos, 0 divergências). Se aparecer CRLF, a correção é `newline="\n"` em cada escrita | |
| W13 | números | XGBoost e scipy compilados com MSVC, outra contagem de threads OpenMP: a última casa decimal pode mudar. Os 4 PNG são `-text` e mostram qualquer byte diferente | mesma checagem. Se divergir, decidir se a garantia "byte a byte" vira "por plataforma" | |
| W15 | `make train` | o *wheel* de Windows do XGBoost traz o OpenMP, mas pode exigir o *Visual C++ Redistributable* | anotar se `import xgboost` funcionou numa máquina limpa | |
| W16 | caminhos longos | o maior caminho versionado tem 59 caracteres, mas `.venv/` e `mlruns/` passam de 260 | rodar uma vez **sem** `core.longpaths` e anotar se algo falha | |

### O que **não** é risco (verificado no Mac)

- **`rm -rf`, `export VAR=`, `source .venv/...`**: não aparecem no Makefile nem
  no README.
- **Caminhos com barra passados como argumento** (`scripts/inspect_data.py`):
  o Python no Windows aceita barra normal. Só quebrava quando o caminho era o
  próprio comando (W6).
- **`uvloop`** aparece no `uv sync` do Mac, mas no `uv.lock` está marcado
  `sys_platform != 'win32'`, e o lock já resolve para `win32`.
- **`mlflow-ui`**: `sqlite:///mlflow.db` é relativo e portável.
- **Fim de linha no checkout**: o `.gitattributes` força LF em Makefile, YAML,
  JSON, Python e MD, qualquer que seja o `core.autocrlf`.

## Roteiro para o teste no Windows

1. PowerShell, **sem** Git Bash no PATH, diretório novo:
   `make install`, `make all`. Medir tempo e disco.
2. A checagem byte a byte do README (W12, W13) — e o `git status`, para ver se
   os dois discordam.
3. `docker compose -f docker-compose.monitoring.yml down -v`, `make stack-up`,
   `make monitor-all`; conferir datasources, dashboards e o Loki (W10).
4. `make validate-bad-batch` (deve sair != 0; `$LASTEXITCODE`),
   `make validate-clean-batch` (0), `make alerts-test`, `make monitor-replay PAUSE=1`.
5. Repetir 1–2 no Git Bash, em outro diretório.
6. Preencher a coluna "status" das duas tabelas.
