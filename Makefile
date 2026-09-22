# Every target runs through `uv run`, so none of them depend on a virtualenv
# being active — `make download` behaves the same in a shell, in CI and in a
# fresh clone. Python version and dependencies come from pyproject.toml and
# uv.lock; .python-version pins the interpreter.

.PHONY: help install download inspect lint test \
        validate-bad-batch validate-clean-batch \
        prepare train recheck-correlation mlflow-ui simulate drift-reports publish-reports aa-test mmd \
        stack-up stack-down stack-logs monitor-replay monitor-all alerts-test dashboards bias seed-noise

# Default target: `make` with no arguments lists what exists.
help:
	@echo "Targets disponíveis:"
	@echo "  make install   - cria o ambiente e instala as dependências (uv sync)"
	@echo "  make download  - baixa o dataset bruto do OpenML e verifica o checksum"
	@echo "  make inspect   - gera reports/inspection.md a partir do dado bruto"
	@echo "  make validate-bad-batch   - gera um lote com defeitos e mostra o portão BLOQUEANDO (sai != 0)"
	@echo "  make validate-clean-batch - valida um lote só com alertas: ACEITO COM AVISOS (sai 0)"
	@echo "  make prepare   - limpa o dado bruto e grava reference + holdout"
	@echo "  make train     - treina os baselines e registra o campeão no MLflow"
	@echo "  make recheck-correlation - recalcula a correlação dos contadores e registra o achado"
	@echo "  make mlflow-ui - abre a UI do MLflow (porta 5001)"
	@echo "  make simulate  - gera seis meses de drift, pontua e escreve o resumo"
	@echo "  make drift-reports - gera os relatórios de drift (em reports/evidently/_build, ignorado)"
	@echo "  make publish-reports - publica o conjunto curado no repositório (ato deliberado)"
	@echo "  make aa-test   - teste A/A e varredura significância vs magnitude"
	@echo "  make mmd       - testes MMD, A/A do MMD e localização por par"
	@echo "  make stack-up  - sobe Prometheus, Pushgateway, Loki, Alloy e Grafana"
	@echo "  make stack-down- derruba a stack (mantém os volumes)"
	@echo "  make monitor-all    - empurra as métricas de todos os lotes de uma vez"
	@echo "  make monitor-replay - empurra mês a mês com pausa, para assistir ao painel"
	@echo "  make alerts-test - valida e testa as regras de alerta (promtool)"
	@echo "  make bias      - mede justiça por faixa etária e gera os gráficos da etapa 4"
	@echo "  make seed-noise - mede o piso de ruído de AUC e KS em cinco sementes"
	@echo "  make lint      - roda o ruff (lint + formatação)"
	@echo "  make test      - roda a suíte de testes (pytest)"

# Installs the project itself too (src layout), which is what makes
# `import credit_monitor` work without a PYTHONPATH hack.
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
# O carimbo de data casa com o nome que scripts/make_dirty_batch.py escreve.
# --------------------------------------------------------------------------
BATCH_STAMP := $(shell date +%Y%m%d)

# Termina com código != 0 DE PROPÓSITO: é o pipeline parando diante de um lote
# que não pode chegar ao modelo. O `make` reporta "Error 1" logo abaixo, e essa
# é a demonstração.
validate-bad-batch:
	uv run python scripts/make_dirty_batch.py --mode dirty
	@uv run python -m credit_monitor.pipeline.ingest \
		data/dirty/batch_$(BATCH_STAMP).parquet ; \
	status=$$? ; \
	echo "" ; \
	echo ">>> ingestão saiu com código $$status — pipeline interrompido, como esperado" ; \
	echo ">>> quarentena: data/quarantine/batch_$(BATCH_STAMP)/" ; \
	echo ">>> relatório : reports/validation/batch_$(BATCH_STAMP).html" ; \
	exit $$status

# Mesmo caminho, lote só com violações de alerta. Tem de sair 0.
validate-clean-batch:
	uv run python scripts/make_dirty_batch.py --mode clean
	@uv run python -m credit_monitor.pipeline.ingest \
		data/clean/clean_batch_$(BATCH_STAMP).parquet ; \
	status=$$? ; \
	echo "" ; \
	echo ">>> ingestão saiu com código $$status — lote aceito, alertas registrados" ; \
	echo ">>> relatório : reports/validation/clean_batch_$(BATCH_STAMP).html" ; \
	exit $$status

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
aa-test:
	uv run python scripts/aa_test.py

# MMD nos lotes, A/A do próprio MMD e localização por par de features.
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
stack-up:
	$(COMPOSE) up -d
	@echo ""
	@echo "Grafana     http://localhost:3000  (anônimo, Viewer)"
	@echo "Prometheus  http://localhost:9090"
	@echo "Pushgateway http://localhost:9091"
	@echo "Loki        http://localhost:3100"

# Sem -v: os volumes sobrevivem, então a série histórica e os logs continuam lá
# no próximo `stack-up`. Apagar dado exige o comando explícito.
stack-down:
	$(COMPOSE) down

stack-logs:
	$(COMPOSE) logs --tail=40

# Empurra tudo de uma vez.
monitor-all:
	uv run python scripts/monitor_push.py --pause 0

# Mês a mês com pausa, para assistir ao painel virando de verde para vermelho.
monitor-replay:
	uv run python scripts/monitor_push.py --pause $(or $(PAUSE),20)

# --------------------------------------------------------------------------
# Regras de alerta: validadas e testadas com o promtool da MESMA versão da
# imagem do Prometheus fixada no compose. Versões diferentes aceitam sintaxes
# diferentes, e um teste que passa contra outra versão não prova nada.
#
# Sem Docker: o binário é baixado para .tools/ (ignorado pelo git), então roda
# igual na máquina e no CI.
# --------------------------------------------------------------------------
PROMETHEUS_VERSION := 3.13.3
PROMTOOL := .tools/promtool

$(PROMTOOL):
	@mkdir -p .tools
	@ARCH=$$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/'); \
	OS=$$(uname -s | tr 'A-Z' 'a-z'); \
	echo "baixando promtool $(PROMETHEUS_VERSION) ($$OS-$$ARCH)"; \
	curl -sfL "https://github.com/prometheus/prometheus/releases/download/v$(PROMETHEUS_VERSION)/prometheus-$(PROMETHEUS_VERSION).$$OS-$$ARCH.tar.gz" \
	  | tar -xz -C /tmp; \
	cp "/tmp/prometheus-$(PROMETHEUS_VERSION).$$OS-$$ARCH/promtool" $(PROMTOOL); \
	chmod +x $(PROMTOOL)

alerts-test: $(PROMTOOL)
	$(PROMTOOL) check rules monitoring/rules/*.rules.yml
	cd monitoring/rules && ../../$(PROMTOOL) test rules credit_monitor.rules.test.yml

# Dashboards geradas, não escritas à mão: JSON de dashboard é escrito por
# máquina e lido por ninguém.
dashboards:
	uv run python scripts/build_dashboards.py
