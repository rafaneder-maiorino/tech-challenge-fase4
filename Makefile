# Every target runs through `uv run`, so none of them depend on a virtualenv
# being active — `make download` behaves the same in a shell, in CI and in a
# fresh clone. Python version and dependencies come from pyproject.toml and
# uv.lock; .python-version pins the interpreter.
#
# Portável entre sh e cmd.exe, porque no Windows o GNU make roda as receitas no
# cmd quando não acha um sh.exe no PATH — o caso padrão num PowerShell. Daí:
# nada de pipeline, `$$?`, aspas simples ou `date` numa receita (lógica com
# shell mora em scripts/*.py), e mensagens via $(info), não `echo "..."`, que
# o cmd imprime com as aspas. Ver docs/windows-notes.md.

# `export` é diretiva do make, não do shell: vale em sh e em cmd. Sem isto, o
# Python no Windows escreve em cp1252 quando a saída é redirecionada
# (`make all > run.log`) e um `→` impresso derruba o alvo com
# UnicodeEncodeError. No macOS e no Linux o locale já é UTF-8: nada muda.
export PYTHONUTF8 := 1

.PHONY: help install download inspect lint test \
        validate-bad-batch validate-clean-batch \
        prepare train recheck-correlation mlflow-ui simulate drift-reports publish-reports aa-test mmd \
        stack-up stack-down stack-urls stack-logs monitor-replay monitor-all \
        alerts-test promtool dashboards bias seed-noise dirty-batch all all-done

# Default target: `make` with no arguments lists what exists.
# $(info) imprime ao expandir a receita; o `@exit 0` final existe porque uma
# receita só de $(info) não roda comando nenhum e o make diria "is up to date".
# `exit 0` é válido em sh e em cmd.
help:
	$(info Targets disponíveis:)
	$(info make all       - a cadeia inteira na ordem certa (~4,5 min, ~1 GB) — comece aqui)
	$(info make install   - cria o ambiente e instala as dependências (uv sync))
	$(info make download  - baixa o dataset bruto do OpenML e verifica o checksum)
	$(info make inspect   - gera reports/inspection.md a partir do dado bruto)
	$(info make dirty-batch  - gera o lote sujo de demonstração se ainda não existir)
	$(info make validate-bad-batch   - gera um lote com defeitos e mostra o portão BLOQUEANDO (sai != 0))
	$(info make validate-clean-batch - valida um lote só com alertas: ACEITO COM AVISOS (sai 0))
	$(info make prepare   - limpa o dado bruto e grava reference + holdout)
	$(info make train     - treina os baselines e registra o campeão no MLflow)
	$(info make recheck-correlation - recalcula a correlação dos contadores e registra o achado)
	$(info make mlflow-ui - abre a UI do MLflow (porta 5001))
	$(info make simulate  - gera seis meses de drift, pontua e escreve o resumo)
	$(info make drift-reports - gera os relatórios de drift (em reports/evidently/_build, ignorado))
	$(info make publish-reports - publica o conjunto curado no repositório (ato deliberado))
	$(info make aa-test   - teste A/A e varredura significância vs magnitude)
	$(info make mmd       - testes MMD, A/A do MMD e localização por par)
	$(info make stack-up  - sobe Prometheus, Pushgateway, Loki, Alloy e Grafana)
	$(info make stack-down - derruba a stack (mantém os volumes))
	$(info make monitor-all    - empurra as métricas de todos os lotes de uma vez)
	$(info make monitor-replay - empurra mês a mês com pausa, para assistir ao painel)
	$(info make alerts-test - valida e testa as regras de alerta (promtool da imagem fixada))
	$(info make bias      - mede justiça por faixa etária e gera os gráficos da etapa 4)
	$(info make seed-noise - mede o piso de ruído de AUC e KS em cinco sementes)
	$(info make lint      - roda o ruff (lint + formatação))
	$(info make test      - roda a suíte de testes (pytest))
	@exit 0

# Installs the project itself too (src layout), which is what makes
# `import credit_monitor` work without a PYTHONPATH hack.
# A cadeia inteira, na ordem em que as dependências exigem. É o alvo para quem
# clonou agora: nenhum passo aqui roda sobre estado deixado por outro dia.
#
# O que NÃO entra, de propósito:
#   - validate-bad-batch, que sai com código != 0 por desenho (o portão
#     bloqueou o lote) e derrubaria o make. O lote sujo que ele valida entra,
#     via dirty-batch: o monitor-all precisa dele;
#   - publish-reports, o único alvo que toca HTML versionado;
#   - stack-up e monitor-all, que precisam do Docker de pé — ficam num alvo
#     próprio para que `make all` funcione sem Docker instalado.
#
# Medido num MacBook (Apple Silicon, cache do uv quente), clone limpo: 271 s
# (~4,5 min) com clone e install, e 967 MB em disco, dos quais 852 MB são o
# .venv. Com o cache frio some ~2 min no install.
#
# A mensagem final vem de all-done, não de um $(info) aqui: o make expande a
# receita inteira antes de rodar a primeira linha, e um $(info) neste alvo
# sairia ANTES da cadeia, não depois.
all:
	$(MAKE) download
	$(MAKE) inspect
	$(MAKE) prepare
	$(MAKE) train
	$(MAKE) simulate
	$(MAKE) dirty-batch
	$(MAKE) drift-reports
	$(MAKE) aa-test
	$(MAKE) bias
	$(MAKE) seed-noise
	$(MAKE) dashboards
	$(MAKE) lint
	$(MAKE) test
	@$(MAKE) --no-print-directory all-done

# Uma linha por comando: `a && b` não existe no Windows PowerShell 5.1.
all-done:
	$(info )
	$(info Cadeia completa. Para a observabilidade (precisa de Docker):)
	$(info make stack-up)
	$(info make monitor-all)
	@exit 0

install:
	uv sync

# Idempotent: skips the network when data/raw/gmsc.parquet already matches the
# checksum recorded in src/credit_monitor/constants.py.
download:
	uv run python -m credit_monitor.data.download

# Read-only over the raw parquet. Requires `make download` first.
inspect:
	uv run python scripts/inspect_data.py

# `check` reports lint findings; `format --check` fails on unformatted code
# without rewriting it, so the target is safe to run in CI.
lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest -q

# --------------------------------------------------------------------------
# Demonstração do portão de qualidade da etapa 1.
#
# Os dois alvos percorrem exatamente o mesmo caminho — mesmo gerador, mesmo
# contrato, mesmo script de ingestão — e só o conteúdo do lote muda. É isso que
# mostra que o portão discrimina, em vez de simplesmente reprovar tudo.
#
# O nome do lote (com a data) e a chamada da ingestão ficam no script: a data
# vinha de `$(shell date +%Y%m%d)`, que no cmd.exe é o comando que TENTA MUDAR
# a data do sistema e pode parar esperando entrada — e, por ser `:=`, rodava a
# cada `make`, inclusive nas sub-makes de `make all`.
# --------------------------------------------------------------------------

# Termina com código != 0 DE PROPÓSITO: é o pipeline parando diante de um lote
# que não pode chegar ao modelo. O `make` reporta "Error 1" logo abaixo, e essa
# é a demonstração.
validate-bad-batch:
	uv run python scripts/make_dirty_batch.py --mode dirty --validate

# Mesmo caminho, lote só com violações de alerta. Tem de sair 0.
validate-clean-batch:
	uv run python scripts/make_dirty_batch.py --mode clean --validate

# Só gera, não valida: sai 0, então cabe no `make all`. É o lote que o
# monitor-all empurra como o caso bloqueado. Preguiçoso, como o monitor-all
# (que chama a mesma função se faltar): um lote já em data/dirty é mantido.
dirty-batch:
	uv run python scripts/make_dirty_batch.py --if-missing

# --------------------------------------------------------------------------
# Etapa 1 — preparação do dado e modelo baseline.
# --------------------------------------------------------------------------

# Limpa, divide em reference/holdout e valida os dois contra o ModelInputSchema.
# A validação roda dentro do pipeline, não só nos testes.
prepare:
	uv run python scripts/prepare_data.py

# Depende de `make prepare`. Nunca toca no holdout: aquele conjunto é o grupo
# de controle do teste A/A da etapa 2.
train:
	uv run python scripts/train_baseline.py

# Recalcula a matriz de correlação dos contadores com e sem as sentinelas e
# reescreve a seção correspondente de docs/findings.md.
recheck-correlation:
	uv run python scripts/recheck_correlation.py

# Porta 5001: no macOS a 5000 é do AirPlay Receiver, que responde à requisição
# em vez de falhar, então o MLflow parece subir e serve um 403 alheio.
mlflow-ui:
	uv run mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5001

# Determinístico: a mesma semente produz os mesmos lotes byte a byte. Depende de
# `make prepare` (para o holdout) e de `make train` (para o alias champion).
simulate:
	uv run python scripts/simulate_production.py

# Depende de `make simulate` (para os lotes) e de `make train` (para o campeão).
# O limiar de operação NÃO é escolhido aqui: vem do KS do campeão, ajustado na
# partição de validação. Um corte escolhido na amostra em que a justiça é
# medida seria um corte escolhido sabendo a resposta.
bias:
	uv run python scripts/bias_analysis.py

# Retreina cinco vezes para medir o ruído do procedimento. NÃO toca no MLflow
# nem no registry: o campeão continua a versão 1 com o alias onde está. O que
# interessa é a dispersão, não um modelo novo.
seed-noise:
	uv run python scripts/seed_noise.py

# Depende de `make simulate` (para os lotes) e de `make train` (para o campeão).
#
# Escreve em reports/evidently/_build/, que é ignorado. Nunca toca no HTML
# versionado: o Evidently nomeia a variável JavaScript do relatório com um UUID
# aleatório a cada execução, então duas gerações do MESMO dado diferem em ~2.300
# posições de byte num arquivo de 4 MB. Gerar direto no caminho versionado faria
# de toda regeneração de rotina um diff de seis arquivos de 4 MB com os mesmos
# números dentro.
drift-reports:
	uv run python scripts/drift_reports.py

# O ÚNICO alvo que mexe em HTML versionado. Publicar é ato deliberado — fim de
# etapa, entrega final —, nunca efeito colateral de olhar um relatório.
publish-reports:
	uv run python scripts/publish_reports.py

# Teste A/A: amostras do holdout contra a referência, onde todo alarme é falso.
# Determinístico pela semente. `--reuse` aproveita o cache em data/drift_tests.
# DEPENDE de `mmd`, e a ordem não é negociável: `aa_test.py` é quem escreve
# reports/drift_tests/summary.md, e ele só preenche a seção de MMD se o cache
# em data/drift_tests/ já existir. Rodando `aa-test` antes de `mmd`, o resumo
# sai com o texto "_Rode `make mmd` para preencher esta seção._" no lugar da
# tabela — que foi exatamente o que aconteceu no teste de clone limpo.
aa-test: mmd
	uv run python scripts/aa_test.py

# MMD nos lotes, A/A do próprio MMD e localização por par de features.
# Escreve só o cache em data/drift_tests/; quem monta o resumo é o aa-test.
mmd:
	uv run python scripts/mmd_test.py

# --------------------------------------------------------------------------
# Etapa 3 — stack de observabilidade.
#
# COMPOSE é definido uma vez: o `-f` é obrigatório porque o arquivo não tem o
# nome padrão, e esquecê-lo num alvo faz o compose falar com o projeto errado.
# --------------------------------------------------------------------------
COMPOSE := docker compose -f docker-compose.monitoring.yml

# Grafana em http://localhost:3000, Prometheus em :9090, Pushgateway em :9091,
# Loki em :3100. Nunca 5000 — no macOS é do AirPlay Receiver.
# Os endereços vêm de stack-urls, chamado depois do `up`: um $(info) aqui
# sairia antes do compose (ver all-done).
stack-up:
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory stack-urls

stack-urls:
	$(info )
	$(info Grafana     http://localhost:3000  (anônimo, Viewer))
	$(info Prometheus  http://localhost:9090)
	$(info Pushgateway http://localhost:9091)
	$(info Loki        http://localhost:3100)
	@exit 0

# Sem -v: os volumes sobrevivem, então a série histórica e os logs continuam lá
# no próximo `stack-up`. Apagar dado exige o comando explícito.
stack-down:
	$(COMPOSE) down

stack-logs:
	$(COMPOSE) logs --tail=40

# Empurra tudo de uma vez. Precisa do campeão (make train) e dos lotes (make
# simulate); o lote sujo, se faltar, é gerado na hora — é o mesmo que
# `make dirty-batch` deixa em data/dirty.
monitor-all:
	uv run python scripts/monitor_push.py --pause 0

# Mês a mês com pausa, para assistir ao painel virando de verde para vermelho.
# A pausa vai como argumento do make — `make monitor-replay PAUSE=5` —, que
# funciona em todo shell; `PAUSE=5 make ...` é sintaxe de sh.
monitor-replay:
	uv run python scripts/monitor_push.py --pause $(or $(PAUSE),20)

# --------------------------------------------------------------------------
# Regras de alerta: validadas e testadas com o promtool da MESMA versão da
# imagem do Prometheus fixada no compose. Versões diferentes aceitam sintaxes
# diferentes, e um teste que passa contra outra versão não prova nada.
#
# scripts/alerts_test.py lê a imagem do próprio compose e roda o promtool de
# dentro dela (Docker), igual em macOS, Linux, Windows e no CI. Sem Docker, usa
# um binário em .tools/ — mas só se ele reportar a versão do compose.
# --------------------------------------------------------------------------
alerts-test:
	uv run python scripts/alerts_test.py

# Só Linux e macOS: baixa o binário nativo para .tools/ (ignorado pelo git),
# para rodar alerts-test sem Docker. Shell POSIX de propósito — no Windows o
# caminho é o Docker. PROMETHEUS_VERSION tem de casar com o compose; se não
# casar, alerts-test recusa o binário em vez de testar contra outra versão.
PROMETHEUS_VERSION := 3.13.3
PROMTOOL := .tools/promtool

promtool: $(PROMTOOL)

$(PROMTOOL):
	@mkdir -p .tools
	@ARCH=$$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/'); \
	OS=$$(uname -s | tr 'A-Z' 'a-z'); \
	echo "baixando promtool $(PROMETHEUS_VERSION) ($$OS-$$ARCH)"; \
	curl -sfL "https://github.com/prometheus/prometheus/releases/download/v$(PROMETHEUS_VERSION)/prometheus-$(PROMETHEUS_VERSION).$$OS-$$ARCH.tar.gz" \
	  | tar -xz -C /tmp; \
	cp "/tmp/prometheus-$(PROMETHEUS_VERSION).$$OS-$$ARCH/promtool" $(PROMTOOL); \
	chmod +x $(PROMTOOL)

# Dashboards geradas, não escritas à mão: JSON de dashboard é escrito por
# máquina e lido por ninguém.
dashboards:
	uv run python scripts/build_dashboards.py
