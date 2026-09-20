# Every target runs through `uv run`, so none of them depend on a virtualenv
# being active — `make download` behaves the same in a shell, in CI and in a
# fresh clone. Python version and dependencies come from pyproject.toml and
# uv.lock; .python-version pins the interpreter.

.PHONY: help install download inspect lint test

# Default target: `make` with no arguments lists what exists.
help:
	@echo "Targets disponíveis:"
	@echo "  make install   - cria o ambiente e instala as dependências (uv sync)"
	@echo "  make download  - baixa o dataset bruto do OpenML e verifica o checksum"
	@echo "  make inspect   - gera reports/inspection.md a partir do dado bruto"
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
