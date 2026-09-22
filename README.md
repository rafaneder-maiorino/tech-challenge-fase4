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
| `-2` | **bloqueado** — o contrato recusou o lote; nenhum drift foi calculado |

Os dois sentinelas são negativos de propósito: ficam fora da ordenação 0..2,
para que um painel ordenando pelo código não leia "amostra insuficiente" ou
"bloqueado" como "menos drift que ok".

`stage_status` segue a mesma lógica: `1` ok, `0` falhou, **`-1` não executou**
porque um estágio anterior bloqueou. Zero seria mentira (o estágio não falhou,
nunca foi tentado) e `1` seria pior.

**Lote bloqueado curto-circuita o pipeline.** Depois de um bloqueio de contrato
nenhum estágio seguinte roda, e nenhuma métrica de drift, de predição ou de
rótulo é publicada — as linhas foram recusadas, e calcular um PSI sobre elas
produziria um número que parece medir a população e mede dado corrompido. O
`verdict` no MLflow é `blocked`.

**Abaixo de `min_batch_size`** (1.000, medido no teste A/A da etapa 2) o
veredito **nunca** é verde nem vermelho — a medição não consegue separar drift
do próprio viés, e pintar qualquer cor apresentaria isso como conhecimento.
`sample_sufficient` vai a 0 no mesmo lote. As duas condições são independentes:
um lote pode ser grande e bloqueado.

### Rótulos chegam atrasados, como na realidade

`label_lag_months` (2, em `configs/monitoring.yaml`) não é conveniência: é a
restrição que define o que o monitor pode saber e quando. No passo 6 do replay
existe desempenho medido para os meses 0 a 4 e **nenhum** para os meses 5 e 6 —
que já têm sinal de drift.

É também o que torna a **janela cega** mensurável: os meses em que uma
degradação real já acontece e nenhum sinal de nenhum tipo é visível. Medido,
com atraso 2: `full` 0 meses, `composition_only` n/a, **`stress_only` 2 meses**.
Ver `docs/findings.md` §13.

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

## Viés, causalidade e Model Card (etapa 4)

Três documentos fecham a fase de construção. Todos seguem a mesma regra: **toda
afirmação aponta para um número medido ou é declarada como ausente.**

### [`docs/vies.md`](docs/vies.md) — viés por faixa etária (Art. 6, IX)

O dataset **não tem raça, sexo nem estado civil**, e nada foi simulado para
preencher a falta — uma disparidade inventada é pior que uma ausente, porque se
parece com evidência. O eixo medido é **idade**, que é critério protegido em
crédito por direito próprio.

O limiar de operação foi **escolhido hoje** (0,0804, o KS do campeão ajustado na
validação), porque até a etapa 3 nenhuma métrica do projeto exigia um corte.

| critério | holdout | mês 6 do `full` |
|---|---|---|
| paridade demográfica | 0,3616 | 0,2037 |
| chances equalizadas | 0,3259 | 0,2139 |
| **calibração por grupo** | **0,0159** | **0,0264** |
| *(a causa)* taxa-base entre faixas | 0,0827 | 0,0407 |

Os três **não podem valer ao mesmo tempo** com taxas-base diferentes — e elas
diferem quatro vezes, de 10,89% na faixa 26-35 a 2,62% na 66+. O sistema
prioriza **calibração por grupo**, e a razão é o Art. 20 §1: o critério
apresentado ao titular é um número, e ele precisa significar a mesma coisa
qualquer que seja a idade de quem o recebe.

Dois resultados que contrariam a expectativa:

1. **A faixa mais prejudicada pelo drift é a mais velha, não a mais jovem.** O
   mecanismo de composição empurra a carteira para os jovens, e quem sai pior é
   quem ficou: 66+ com gap de **-0,0730** no mês 6 (IC 95% [-0,1112, -0,0403]),
   quase três vezes o da faixa 18-25.
2. **O mecanismo que move a demografia não é o que causa a injustiça.** A
   ablação separa: `composition_only` estraga a calibração de **uma** faixa;
   `stress_only` estraga a de **todas as seis**. E o painel de drift enxerga o
   primeiro (PSI 0,93) e é cego ao segundo (PSI 0,0082).

Daí uma regra de método: **justiça de grupo se reporta com o nível por faixa, não
só com a diferença entre faixas.** O braço com doze vezes mais dano agregado de
calibração tem o *menor* spread entre faixas — porque ninguém escapou, e
diferença é cega para falha de modo comum.

| | |
|---|---|
| ![Aprovação por faixa](reports/fairness/approval_by_band.png) | ![Chances equalizadas](reports/fairness/odds_by_band.png) |

![Calibração por faixa](reports/fairness/calibration_by_band.png)

`make bias` reproduz tudo: os três PNG e `reports/fairness/bands.json`, de onde
sai cada número do documento.

### [`docs/causalidade.md`](docs/causalidade.md) — intervenção, não correlação

Quase toda "análise causal" em produção é uma associação com vocabulário melhor,
porque falta o passo de desligar o mecanismo e olhar de novo. **Aqui esse passo
existe:** o processo gerador é nosso, os meses 0 a 6 são gerados quatro vezes com
a **mesma semente**, e ligar um mecanismo é `do(M)` no sentido de Pearl, com as
outras vias fechadas por construção.

O documento traz o DAG com **cada aresta rotulada pelo efeito medido**, e mantém
a refutação em destaque: a história desenhada atribuía a degradação silenciosa à
inflação nominal, e a intervenção diz que o **estresse domina — -0,0337 contra
-0,0049 de gap**. A razão é que o campeão tira apenas **2,15%** do ganho da
renda. **O DAG acertou a direção e errou a magnitude**, e está mantido como foi
escrito, com a refutação ao lado.

O pagamento prático é uma **tabela de decisão** que identifica o mecanismo pela
assinatura — drift com calibração intacta é composição, calibração rompida sem
drift é conceito, nem um nem outro com contrato violado é a origem do dado — com
cada linha ligada ao runbook que já existe.

E a fronteira, dita explicitamente: os mecanismos foram escolhidos por nós, então
a análise identifica efeitos **dentro do mundo simulado**. A estrutura causal é
real; as magnitudes são nossas.

### [`docs/model_card.md`](docs/model_card.md) — o Model Card

Estrutura padrão, e carrega o que costuma ser escondido:

- **a margem do campeão contra o piso de ruído.** +0,0070 de AUC contra ±0,0070
  de desvio em cinco sementes: **limítrofe** — uma sexta semente poderia inverter
  a ordem. No KS a vantagem é mais clara (+0,0185), com a ressalva de que o ruído
  do KS não foi medido;
- **calibração como argumento de justiça.** A reponderação infla as
  probabilidades ~14x: o ponto de corte do KS fica em **0,4787** no modelo
  reponderado e em **0,0804** no calibrado — a mesma decisão, anunciada como 48%
  num caso e 8% no outro, e é esse número que o Art. 20 §1 apresenta ao titular
  como "o critério";
- **as 269 linhas de sentinela excluídas do treino.** Inadimplem a **54,65%** e
  são exatamente a população para a qual um sistema em produção mais precisa de
  uma decisão. Contenção de escopo, **não** problema resolvido;
- **o perfil de degradação:** AUC 0,8601 → 0,7779 e gap -0,0027 → -0,0396 em seis
  meses, com o *lead time* negativo nos dois cenários — **este monitor nunca
  avisa antes do dano**;
- **manutenção:** qual alerta dispara qual decisão, e o fato de que nenhuma
  decisão que dependa de calibração pode ser tomada antes dos **dois meses** de
  atraso do rótulo.

## Governança, privacidade e LGPD (etapa 4)

**Documento completo: [`docs/governanca.md`](docs/governanca.md).**

O projeto usa o *Give-Me-Some-Credit* (OpenML id 45577), **já anonimizado na
origem e sem coluna de identificador**. Não havendo pessoa natural identificada
nem identificável, não há dado pessoal — e o **Art. 12** põe o tratamento fora
da LGPD. É esse o motivo, e é o único: **não** é por o dado ser público (o Art.
7 §3 é expresso em dizer que publicidade não remove proteção) nem por o projeto
ser acadêmico (o Art. 4, II, *b* é uma porta estreita e não é esta). A diferença
importa porque as duas justificativas erradas não sobreviveriam à mudança de
contexto: no dia em que este mesmo pipeline receber a proposta de uma pessoa
real, o tratamento vira dado pessoal sem que uma linha de código mude.

Por isso `docs/governanca.md` descreve **o regime que se aplicaria ao mesmo
pipeline rodando sobre candidatos reais**, com duas camadas que nunca se
misturam:

- **[IMPLEMENTADO]** — o que o repositório faz hoje, sempre com arquivo, função,
  configuração ou teste que se pode abrir e conferir.
- **[PRESCRITO]** — o que um sistema com titulares reais precisaria além disso,
  e que aqui **não existe**.

O que está lá:

| seção | conteúdo |
|---|---|
| §1 | inventário das 11 colunas — nenhuma é sensível pelo Art. 11, **nenhuma é identificador** (verificado na inspeção do dia 1), e `age` + renda + dependentes + linhas abertas formam uma combinação quase-identificadora |
| §2 | base legal: **proteção ao crédito (Art. 7, X)**, e por que consentimento é o pior encaixe — não é livre quando dá acesso a crédito, e é revogável, o que esvaziaria a própria referência de drift |
| §2.2 | **Art. 20** (revisão e critérios) ligado ao que já existe: registry com alias `champion`, `dataset_sha256` por run, participação no ganho por feature, probabilidade **calibrada** |
| §3 | retenção por artefato, com a razão de cada prazo |
| §4 | os sete princípios de *Privacy by Design*, cada um apontando para um caminho de arquivo |
| §5 | o hash salgado dos logs: **pseudonimização (Art. 13), não anonimização** |
| §6 | as limitações, reunidas |

Três pontos que o documento **argumenta** em vez de afirmar:

1. **A quarentena é a tensão mais aguda — e foi medida, não suposta.**
   `rows.parquet` guarda **12 linhas × as 11 colunas**, incluindo a combinação
   quase-identificadora inteira e o rótulo; `rejections.jsonl` traz o valor em
   claro em **8 dos 13** registros (`age: 12`, `MonthlyIncome: -1500.0`). É o
   oposto da minimização, de propósito: sem essa evidência ninguém distingue "a
   pessoa mandou dado errado" de "o nosso contrato está errado", e a rejeição
   deixa de ser contestável. É também o **único** ponto do pipeline que escreve
   valor de feature em arquivo de texto — os logs levantam exceção se alguém
   tentar. A exceção paga por si com a retenção mais curta da tabela.
2. **Monitoramento precisa de história.** Uma referência apagada não é
   substituível, e o sistema não acusa erro — passa a medir drift contra uma
   base nova e reporta que está tudo bem. A reconciliação é guardar a
   **estatística** (bordas de bin, quantis, distribuições nulas) e deixar as
   **linhas** seguirem o prazo do dado operacional.
3. **Calibrar é uma questão de justiça antes de ser técnica.** Reponderar a
   classe infla as probabilidades ~14x sem mexer no AUC nem no KS: a pessoa é
   recusada por um número que não significa o que diz, e é justamente esse
   número que o Art. 20 §1 lhe apresentaria como "o critério".

A camada [PRESCRITO] é grande de propósito. A maior parte da conformidade com a
LGPD é processo organizacional — encarregado, canal do titular, revisão humana,
resposta a incidente — e isso não mora num repositório de código.

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
