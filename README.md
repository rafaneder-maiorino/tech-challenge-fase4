# Tech Challenge — Fase 4: Monitoramento de Modelo de Credit Scoring

[![CI](https://github.com/rafaneder-maiorino/tech-challenge-fase4/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rafaneder-maiorino/tech-challenge-fase4/actions/workflows/ci.yml)

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
make prepare    # limpa o dado bruto e grava reference + holdout
make train      # treina os baselines e registra o campeão no MLflow
make lint       # roda o ruff (lint + verificação de formatação)
make test       # roda a suíte de testes (pytest)
```

Demonstração do portão de qualidade e achados:

```bash
make validate-bad-batch     # lote com defeitos: verdicto BLOCKED, sai != 0
make validate-clean-batch   # lote só com alertas: ACCEPTED_WITH_WARNINGS, sai 0
make recheck-correlation    # recalcula a correlação dos contadores (docs/findings.md)
make mlflow-ui              # UI do MLflow na porta 5001
```

> **macOS:** o XGBoost precisa do OpenMP em tempo de execução, que não vem no
> wheel. `brew install libomp` antes do `make train`. E a UI do MLflow usa a
> porta **5001**, não a 5000: no macOS a 5000 é do AirPlay Receiver, que
> responde à requisição em vez de recusá-la, então o MLflow parece subir e
> serve um 403 alheio.

## Relatórios versionados: gerar não é publicar

`make drift-reports` escreve em `reports/evidently/_build/`, que é **ignorado**.
`make publish-reports` é o **único** comando que toca nos HTML versionados.

A separação não é preciosismo. O HTML do Evidently não é determinístico byte a
byte: a variável JavaScript do relatório recebe um UUID aleatório a cada
execução, e duas gerações do **mesmo** dado diferem em ~2.300 posições num
arquivo de 4 MB. Gerar direto no caminho versionado faria de toda regeneração de
rotina um diff de seis arquivos de 4 MB com os mesmos números dentro — e um
`git commit -a` distraído põe isso no histórico para sempre.

**Publique em pontos deliberados** — fim de etapa, entrega final. Nunca como
efeito colateral de olhar um relatório. Depois de publicar, confira o diff antes
de commitar.

## Stack de observabilidade (etapa 3)

```bash
make stack-up        # Prometheus, Pushgateway, Loki, Alloy e Grafana
make monitor-all     # empurra as métricas de todos os lotes de uma vez
make monitor-replay  # mês a mês, com pausa (PAUSE=20 por padrão)
make stack-down      # derruba, mantendo os volumes
```

| serviço | porta | papel |
|---|---|---|
| Grafana | 3000 | lê Prometheus e Loki |
| Prometheus | 9090 | armazena as métricas |
| Pushgateway | 9091 | recebe de job em lote |
| Loki | 3100 | armazena os logs |
| Alloy | 12345 | recolhe `logs/*.jsonl` |
| MLflow UI | 5001 | trilha de auditoria (`make mlflow-ui`) |

Nenhuma porta 5000: no macOS é do AirPlay Receiver, que **responde** à
requisição em vez de recusá-la. Toda imagem fixada em versão exata.

### Push, não scrape

O trabalho de drift é um lote: roda, reporta, termina. Não há processo de pé
para o Prometheus raspar. O Pushgateway é a ponte — e ele **recusa timestamp do
cliente**, marcando tudo com o instante do scrape. Empurrar sete meses em
sequência colapsaria a rampa num único instante.

A saída não é um timestamp, é um rótulo: a chave de agrupamento é
`{job, scenario, batch_id}`, então cada mês persiste como série própria e o
Grafana plota a rampa por rótulo. `scenario` carrega os braços da ablação da
etapa 2, para o 2x2 caber numa tela.

### `drift_verdict`: os códigos

O Prometheus guarda float64, então o veredito é um número:

| código | significado |
|---|---|
| `0` | ok — nenhuma feature cruzou 0,10 |
| `1` | alerta — alguma feature em [0,10; 0,25) |
| `2` | crítico — alguma feature em 0,25 ou acima |
| `-1` | **amostra insuficiente** — lote abaixo de `min_batch_size` |

`-1` e não `3` de propósito: fora da ordenação 0..2, para que um painel
ordenando pelo código não leia "amostra insuficiente" como "menos drift que
ok". Abaixo de `min_batch_size` (1.000, medido no teste A/A da etapa 2) o
veredito **nunca** é verde nem vermelho — a medição não consegue separar drift
do próprio viés, e pintar qualquer cor apresentaria isso como conhecimento.
`sample_sufficient` vai a 0 no mesmo lote.

### Logs: sem dado pessoal, nunca

Cada estágio escreve uma linha JSON em `logs/`, com `batch_id`, `scenario` e
`stage` promovidos a rótulo do Loki. **Valor de feature nunca entra num log** —
um log é o artefato menos controlado que um pipeline produz, e as features aqui
são renda, idade e dívida de uma pessoa. A identidade da linha viaja como
**hash salgado do row_id**: o suficiente para correlacionar duas linhas sobre a
mesma linha de dado, insuficiente para reconstruir qualquer coisa sobre a
pessoa. Tentar registrar um campo de feature levanta exceção.

### Prometheus é operação, MLflow é auditoria

A mesma execução escreve nos dois, de propósito. O Prometheus responde "o que
está acontecendo agora e o que deveria acordar alguém"; o MLflow responde "o
que foi decidido, sobre qual dado, por qual versão do modelo". Reconstruir
qualquer um dos dois a partir do outro é chute.

## Notas de contribuição

**Todo dia termina atualizando [`docs/video-notes.md`](docs/video-notes.md)** com
qualquer novo candidato a achado-herói ou a tomada de demonstração.

O motivo é de curadoria, não de burocracia: `docs/findings.md` guarda tudo e já
passa de dez achados, mas o vídeo comporta cerca de quatro. Decidir o que entra
no fim, com tudo pronto, é decidir por cansaço. Decidir no dia em que o achado
aparece — enquanto o número ainda está fresco e o artefato ainda está na tela —
é decidir com informação.

Vale também para o que **não** entra: a seção "Descartados" registra por que cada
achado ficou de fora, e essa lista é o que impede a discussão de recomeçar do
zero a cada semana.

Pré-requisitos: [uv](https://docs.astral.sh/uv/) e Python 3.11 (fixado em
`.python-version`).

`make download` é idempotente: se `data/raw/gmsc.parquet` já existir e seu
SHA-256 bater com o valor registrado em `src/credit_monitor/constants.py`, o
download é pulado. Se não bater, o arquivo é baixado de novo e o aviso aparece
no log.
