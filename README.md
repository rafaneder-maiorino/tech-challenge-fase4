# Tech Challenge — Fase 4: Monitoramento de Modelo de Credit Scoring

Camada de sustentação e confiabilidade para um modelo de *credit scoring* em
produção. O projeto usa o dataset público **Give-Me-Some-Credit** (OpenML id
45577) e cobre contratos de qualidade de dados, simulação e detecção estatística
de *data drift* e *concept drift*, observabilidade de pipeline e modelo, e a
documentação de governança e privacidade (LGPD). Esta primeira etapa entrega o
esqueleto do projeto, o download reprodutível do dado bruto — verificado por
SHA-256 — e um relatório factual de inspeção que serve de base para definir as
regras do contrato de dados.

## Comandos

```bash
make install    # cria o ambiente e instala as dependências (uv sync)
make download   # baixa o dataset bruto do OpenML e verifica o checksum
make inspect    # gera reports/inspection.md a partir do dado bruto
make lint       # roda o ruff (lint + verificação de formatação)
make test       # roda a suíte de testes (pytest)
```

Pré-requisitos: [uv](https://docs.astral.sh/uv/) e Python 3.11 (fixado em
`.python-version`).

`make download` é idempotente: se `data/raw/gmsc.parquet` já existir e seu
SHA-256 bater com o valor registrado em `src/credit_monitor/constants.py`, o
download é pulado. Se não bater, o arquivo é baixado de novo e o aviso aparece
no log.
