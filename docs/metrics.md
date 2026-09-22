# Métricas de saúde — catálogo

Toda métrica que a stack publica: o que é, de onde vem, em que unidade, qual o
limiar, **como o limiar foi obtido** e por que vale acompanhar.

A última coluna é a que costuma faltar. Um limiar sem procedência é um número
que alguém escolheu numa reunião, e quando ele dispara ninguém sabe se o
problema é o dado ou o limiar. Todos os daqui vieram de uma medição, e cada um
aponta para onde a medição está.

---

## 1. Os dois momentos

As métricas se dividem por **quando o dado existe**, não por assunto:

| job | quando | o que vê |
|---|---|---|
| `credit_monitor_scoring` | no instante da pontuação | features e score. **Sem rótulo.** |
| `credit_monitor_labels` | `label_lag_months` depois | desempenho real |

A separação não é organizacional, é física: no momento da pontuação o desfecho
não aconteceu. Toda a "janela cega" da etapa 3 sai daí.

Todas as séries são identificadas por `{job, scenario, batch_id}` — o
Pushgateway recusa timestamp do cliente, então o mês vive num rótulo e não no
eixo do tempo.

---

## 2. Pipeline

| métrica | unidade | definição | limiar | origem do limiar |
|---|---|---|---|---|
| `rows_in` | linhas | linhas recebidas no lote | — | — |
| `rows_quarantined` | linhas | linhas que violaram regra bloqueante | > 0 investiga | severidade do contrato (etapa 1) |
| `batch_size` | linhas | linhas efetivamente pontuadas | — | — |
| `sample_sufficient` | 0/1 | 1 se `batch_size >= min_batch_size` | `== 0` alerta info | **medido**: A/A da etapa 2 |
| `stage_duration_seconds{stage}` | s | duração de cada estágio | — | — |
| `stage_status{stage}` | código | `1` ok · `0` falhou · `-1` **pulado** | `== 0` crítico | binário |
| `contract_violations{rule,severity}` | linhas | linhas por regra violada | `severity="blocker"` crítico | `contracts/severity.py` |
| `last_success_timestamp_seconds` | unix | última execução bem-sucedida | — | — |

**Por que `-1` para pulado.** Um estágio que nunca rodou não falhou e não teve
sucesso. `0` seria mentira e `1` seria pior. O valor distinto é o que permite
ao painel mostrar "pulado" em vez de inventar um estado.

---

## 3. Drift (sem rótulo)

| métrica | unidade | definição | limiar | origem do limiar |
|---|---|---|---|---|
| `drift_psi{feature}` | PSI | PSI contra a referência da etapa 1, 10 bins de quantil | 0,10 alerta · 0,25 crítico | **medido**: §4.1 |
| `drift_psi_weighted{feature}` | PSI×ganho | PSI × participação da feature no ganho do campeão | ordenação, não limiar | dia 7 |
| `feature_gain_share{feature}` | fração | participação da feature no ganho do XGBoost | `>= 0,10` eleva severidade | importância do campeão |
| `drifted_features{level}` | contagem | features em cada faixa | — | derivada |
| `prediction_psi` | PSI | PSI da distribuição de score | 0,10 alerta | mesma calibração |
| `prediction_mean` | probabilidade | probabilidade média prevista | — | — |
| `drift_verdict` | código | `0` ok · `1` alerta · `2` crítico · `-1` amostra insuficiente · `-2` **bloqueado** | — | derivado do PSI |

**Por que o ponderado existe.** Um PSI de 0,30 numa feature que carrega 28% do
ganho e o mesmo 0,30 numa que carrega 2% não são o mesmo evento. No mês 6 do
cenário `full`, `age` tem PSI **0,314** e 3,29% do ganho, enquanto
`NumberOfTimes90DaysLate` tem PSI **0,302** e **28,47%** — ordenar por PSI cru
inverte a prioridade. A regra `FeatureDriftCritical` exige as duas condições
justamente por isso.

**Por que os sentinelas são negativos.** `-1` e `-2` ficam fora da ordenação
`0..2`, para que um painel ordenando pelo código não leia "amostra
insuficiente" ou "bloqueado" como "menos drift que ok".

---

## 4. Saúde do modelo (só com rótulo)

| métrica | unidade | definição | limiar | origem do limiar |
|---|---|---|---|---|
| `auc` | AUC-ROC | ordenação no lote | `<= 0,8372` alerta | **medido**: §4.2 |
| `brier` | Brier | erro quadrático da probabilidade | — (acompanhado) | dia 4 |
| `calibration_gap` | probabilidade | previsto médio **menos** observado | `<= -0,0056` crítico | **medido**: §4.2 |
| `observed_default_rate` | fração | inadimplência observada | — | — |
| `label_lag_months` | meses | atraso entre pontuação e desfecho | — | configuração |
| `labels_pending` | 0/1 | lote pontuado sem desfecho | — | — |
| `labels_pending_batches` | contagem | `sum by (scenario) (labels_pending)` | `> 0` info | derivada |

### 4.1 Limiares de drift — teste A/A da etapa 2

200 sorteios do holdout por tamanho, comparados contra a referência completa,
**sem drift algum**: todo alarme é falso por construção. No tamanho de lote de
produção (~7.000 linhas):

| regra | falso alarme medido |
|---|---|
| KS sem correção | **21,5%** |
| KS + Bonferroni | 1,5% |
| PSI > 0,10 | **0,0%** |
| PSI > 0,25 | **0,0%** |

Zero em 200 sorteios limita a taxa real a menos de **1,5% com 95% de
confiança**. É por isso que **o PSI decide o veredito e o KS é diagnóstico**.

E os limiares **não são transportáveis entre tamanhos de lote**: abaixo de
n ≈ 500 a ordem se inverte e o PSI vira o pior dos dois (28,5% de falso alarme
a n = 250). Daí o `min_batch_size = 1000`. Detalhe em `docs/findings.md` §11.

### 4.2 Limiares de saúde — bootstrap do dia 10

Mesmo método, aplicado ao gap e ao AUC: **500 sorteios de 7.000 linhas** do
holdout, pontuados pelo campeão, sem drift nenhum. Os limiares saem da
distribuição nula resultante.

| | gap de calibração | AUC |
|---|---|---|
| média do nulo | +0,00075 | 0,85871 |
| desvio do nulo | 0,00241 | 0,00857 |
| percentil escolhido | **0,5%** | **1%** |
| limiar | **-0,0056** | **0,8372** |
| falso alarme medido | **0,6%** | **1,0%** |

A assimetria é deliberada: o gap é **crítico** e acorda alguém, então paga um
percentil mais apertado; o AUC é **alerta** e pode ser mais sensível.

O limiar provisório de -0,015 tinha falso alarme de **0,0%** — seguro e cego,
6,5 desvios abaixo da média nula. Trocá-lo mudou a janela cega do cenário
`full` de +1 para **-1** mês.

---

## 5. Por que estas e não outras

**O gap de calibração é a métrica central**, e é a menos óbvia. AUC e KS medem
ordenação, e ordenação é invariante a qualquer transformação monotônica do
score — as duas sobrevivem a um deslocamento que move todo mundo junto. O gap é
a única que nota quando as probabilidades param de significar o que dizem.

A etapa 2 mediu exatamente isso: no braço `stress_only`, o PSI máximo fica em
**0,008** — doze vezes abaixo do limiar — enquanto o gap chega a **-0,0337**.
Todo painel de drift verde, o modelo medidamente pior.

**O corolário é o que justifica a stack inteira**: vigiar features não pode,
sozinho, pegar o mecanismo que causa a maior parte do dano de calibração.
Monitoramento baseado em rótulo é **obrigatório, não complementar** — e é o mais
lento, o que é precisamente por que `labels_pending_batches` existe: para que a
lentidão apareça na tela.

---

## 6. Onde cada número foi medido

| assunto | documento |
|---|---|
| 2x2 (drift ≠ degradação) | `docs/findings.md` §9 |
| limiares de drift e falso alarme | `docs/findings.md` §11 · `reports/drift_tests/summary.md` |
| MMD e ordenação por dano | `docs/findings.md` §12 |
| janela cega e lead time | `docs/findings.md` §13 |
| PSI × ganho | `reports/evidently/summary.md` §4 |
| cenário e ablação | `docs/simulation.md` · `reports/simulation/summary.md` |
| runbooks por alerta | `docs/runbooks/` |
