# Simulação de drift — resumo da execução

Gerado por `make simulate`. Determinístico: a mesma semente produz os mesmos
lotes byte a byte. O cenário, o DAG e o raciocínio estão em
[`docs/simulation.md`](../../docs/simulation.md); este arquivo é só o resultado.

- Fonte: `data/processed/holdout.parquet` (44,208 linhas). O
  `reference` nunca é tocado.
- Campeão: `models:/credit-default-baseline@champion`
- Lotes escritos em `data/production/month_XX/` — três arquivos por mês
  (`features`, `predictions`, `labels`), porque em produção eles chegam em
  momentos diferentes.

---

## 1. Parâmetros congelados

| parâmetro | valor |
|---|---|
| `seed` | 20260921 |
| `n_months` | 6 |
| `batch_size` | 7368 |
| `composition.b_util` | 0.9 |
| `composition.b_age` | 0.45 |
| `composition.b_d30` | 0.7 |
| `composition.b_d60` | 0.6 |
| `composition.b_d90` | 0.6 |
| `composition.lag_d60_months` | 2 |
| `composition.lag_d90_months` | 3 |
| `inflation.pi_total` | 0.1 |
| `stress.extra_default_rate_at_full` | 0.03 |
| `multivariate.tolerance` | 0.02 |
| `multivariate.pairs` | primary_by_dependence: NumberOfOpenCreditLinesAndLoans / NumberRealEstateLoansOrLines -> -0.46, credit_tightening_channel: RevolvingUtilizationOfUnsecuredLines / age -> 0.27 |
| `mechanisms.composition` | True |
| `mechanisms.inflation` | True |
| `mechanisms.stress` | True |
| `psi.bins` | 10 |

---

## 2. A rampa de PSI

Dez bins de quantil cortados no `reference`. 🟢 < 0,10 · 🟡 0,10 a 0,25 · 🔴 > 0,25.

| mês | RevolvingUtilizationOfUnsecuredLines | age | NumberOfTime30-59DaysPastDueNotWorse | DebtRatio | MonthlyIncome | NumberOfOpenCreditLinesAndLoans | NumberOfTimes90DaysLate | NumberRealEstateLoansOrLines | NumberOfTime60-89DaysPastDueNotWorse | NumberOfDependents | income_missing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **0** | 0.001 🟢 | 0.001 🟢 | 0.004 🟢 | 0.001 🟢 | 0.002 🟢 | 0.001 🟢 | 0.003 🟢 | 0.003 🟢 | 0.001 🟢 | 0.001 🟢 | 0.000 🟢 |
| **1** | 0.039 🟢 | 0.013 🟢 | 0.013 🟢 | 0.002 🟢 | 0.002 🟢 | 0.005 🟢 | 0.006 🟢 | 0.005 🟢 | 0.004 🟢 | 0.005 🟢 | 0.000 🟢 |
| **2** | 0.112 🟡 | 0.052 🟢 | 0.055 🟢 | 0.005 🟢 | 0.004 🟢 | 0.007 🟢 | 0.017 🟢 | 0.009 🟢 | 0.016 🟢 | 0.006 🟢 | 0.001 🟢 |
| **3** | 0.250 🟡 | 0.101 🟡 | 0.111 🟡 | 0.008 🟢 | 0.012 🟢 | 0.017 🟢 | 0.036 🟢 | 0.011 🟢 | 0.041 🟢 | 0.011 🟢 | 0.004 🟢 |
| **4** | 0.500 🔴 | 0.189 🟡 | 0.217 🟡 | 0.015 🟢 | 0.012 🟢 | 0.042 🟢 | 0.116 🟡 | 0.021 🟢 | 0.117 🟡 | 0.018 🟢 | 0.007 🟢 |
| **5** | 0.694 🔴 | 0.251 🔴 | 0.329 🔴 | 0.019 🟢 | 0.017 🟢 | 0.052 🟢 | 0.214 🟡 | 0.027 🟢 | 0.201 🟡 | 0.019 🟢 | 0.011 🟢 |
| **6** | 0.942 🔴 | 0.314 🔴 | 0.442 🔴 | 0.024 🟢 | 0.022 🟢 | 0.085 🟢 | 0.302 🔴 | 0.044 🟢 | 0.278 🔴 | 0.020 🟢 | 0.013 🟢 |

### Alvos de calibração

| alvo | atingido |
|---|---|
| mês 0: toda feature < 0,10 | ✅ |
| mês 1: toda feature < 0,10 | ✅ |
| mês 3: ao menos duas features em [0,10; 0,25] | ✅ |
| mês 6: ao menos duas features > 0,25 | ✅ |

Os pesos foram ajustados **só** contra estes quatro alvos e então congelados.
Duas variantes mais suaves (`b_util` 0,70 e 0,55) foram testadas e **falham** o
alvo do mês 3 — com elas apenas uma feature cai na faixa 🟡 — sem reduzir de
forma apreciável a taxa de inadimplência final (23,1% e 21,7% contra 24,5%). O
conjunto congelado é o único dos três que atinge os quatro alvos.

---

## 3. Métricas por lote

| mês | linhas | AUC-ROC | KS | Brier | inadimplência observada | previsto médio | **gap de calibração** |
|---|---|---|---|---|---|---|---|
| 0 | 7,368 | 0.8601 | 0.5701 | 0.0519 | 7.08% | 6.81% | **-0.0027** |
| 1 | 7,368 | 0.8507 | 0.5596 | 0.0664 | 9.39% | 8.49% | **-0.0091** |
| 2 | 7,368 | 0.8404 | 0.5374 | 0.0749 | 10.82% | 10.21% | **-0.0061** |
| 3 | 7,368 | 0.8204 | 0.4970 | 0.0943 | 13.95% | 12.23% | **-0.0173** |
| 4 | 7,368 | 0.8253 | 0.5211 | 0.1127 | 17.78% | 15.68% | **-0.0210** |
| 5 | 7,368 | 0.7973 | 0.4614 | 0.1365 | 21.67% | 18.47% | **-0.0321** |
| 6 | 7,368 | 0.7779 | 0.4285 | 0.1529 | 24.46% | 20.50% | **-0.0396** |

O **gap de calibração** é o sinal de degradação silenciosa, e a tabela mostra
por quê: o AUC cai 9.6% em seis meses enquanto a
inadimplência observada mais que **triplica**. Um painel que vigie só
ordenação vê uma degradação moderada; a realidade é que o modelo continua
prevendo 20.5% onde o observado é
24.5%.

---

## 4. Ablação — uma intervenção, não uma correlação

Os meses 0 a 6 gerados quatro vezes com a **mesma semente**, variando só quais
mecanismos estão ligados. Como controlamos o processo gerador, ligar e desligar
um mecanismo é intervir, não observar uma associação (ver
`docs/simulation.md` §6).

### AUC-ROC

| configuração | mês 0 | mês 1 | mês 2 | mês 3 | mês 4 | mês 5 | mês 6 |
|---|---|---|---|---|---|---|---|
| `all` | 0.8601 | 0.8507 | 0.8404 | 0.8204 | 0.8253 | 0.7973 | 0.7779 |
| `composition_only` | 0.8601 | 0.8638 | 0.8482 | 0.8455 | 0.8437 | 0.8294 | 0.8130 |
| `inflation_only` | 0.8601 | 0.8625 | 0.8528 | 0.8563 | 0.8594 | 0.8582 | 0.8658 |
| `stress_only` | 0.8601 | 0.8475 | 0.8190 | 0.8157 | 0.8048 | 0.8024 | 0.7917 |

### Gap de calibração

| configuração | mês 0 | mês 1 | mês 2 | mês 3 | mês 4 | mês 5 | mês 6 |
|---|---|---|---|---|---|---|---|
| `all` | -0.0027 | -0.0091 | -0.0061 | -0.0173 | -0.0210 | -0.0321 | -0.0396 |
| `composition_only` | -0.0027 | -0.0041 | +0.0032 | -0.0001 | -0.0000 | -0.0007 | -0.0028 |
| `inflation_only` | -0.0027 | -0.0019 | +0.0010 | +0.0014 | -0.0022 | -0.0004 | -0.0049 |
| `stress_only` | -0.0027 | -0.0052 | -0.0060 | -0.0171 | -0.0183 | -0.0279 | -0.0337 |

### Inadimplência observada

| configuração | mês 0 | mês 1 | mês 2 | mês 3 | mês 4 | mês 5 | mês 6 |
|---|---|---|---|---|---|---|---|
| `all` | 7.08% | 9.39% | 10.82% | 13.95% | 17.78% | 21.67% | 24.46% |
| `composition_only` | 7.08% | 8.93% | 9.91% | 12.32% | 15.97% | 18.89% | 21.00% |
| `inflation_only` | 7.08% | 7.03% | 6.51% | 6.60% | 6.88% | 6.51% | 7.00% |
| `stress_only` | 7.08% | 7.40% | 7.26% | 8.43% | 8.55% | 9.58% | 10.04% |

### Previsto médio

| configuração | mês 0 | mês 1 | mês 2 | mês 3 | mês 4 | mês 5 | mês 6 |
|---|---|---|---|---|---|---|---|
| `all` | 0.0681 | 0.0849 | 0.1021 | 0.1223 | 0.1568 | 0.1847 | 0.2050 |
| `composition_only` | 0.0681 | 0.0852 | 0.1023 | 0.1232 | 0.1597 | 0.1883 | 0.2072 |
| `inflation_only` | 0.0681 | 0.0684 | 0.0661 | 0.0673 | 0.0666 | 0.0648 | 0.0652 |
| `stress_only` | 0.0681 | 0.0688 | 0.0666 | 0.0672 | 0.0672 | 0.0679 | 0.0667 |

### O que a intervenção mostra — e o que ela refuta

A tabela separa os três tipos de drift de forma limpa:

- **`composition_only` (drift de dados, P(X))** degrada a **ordenação** e
  quase não toca a **calibração**: AUC
  0.8601 → 0.8130
  com gap final de apenas -0.0028.
  Faz sentido: os rótulos são reais, então clientes mais arriscados de fato
  inadimplem mais e as probabilidades do modelo continuam certas *para eles*.
- **`stress_only` (drift de conceito, P(y|X))** quebra as duas coisas: AUC
  0.8601 → 0.7917
  e gap -0.0337.
- **`inflation_only` (drift por medição)** é **quase inerte**, e isso
  **contradiz a história desenhada**. O DAG atribuía a degradação silenciosa a
  este mecanismo. A intervenção mostra que a direção está certa e a magnitude
  não: o previsto médio cai de
  0.0681 para
  0.0652 — o modelo realmente passa a
  ler risco menor, como o DAG prevê — mas são
  0.0029
  de probabilidade, e o AUC não se move.

A razão é **o quanto o campeão usa renda**, medido em estatísticas que a cauda
não contamina (`MonthlyIncome` tem desvio 14.483 e máximo 3.008.750, então
Pearson não serve aqui — ver `docs/findings.md` §8):

| evidência | `MonthlyIncome` | topo da lista | posição |
|---|---|---|---|
| importância por permutação (queda de AUC) | **+0,0029** | utilização, +0,0757 | 7 de 11 |
| ganho do XGBoost | **2,15%** | 90d+, 28,47% | 9 de 11 |
| AUC univariada (discriminação) | 0,5746 | utilização, 0,7770 | 6 de 11 |

A renda tem sinal univariado real — discriminação 0,5746, meio da tabela — mas
**o modelo quase não a usa**: 2,15% do ganho, e embaralhar a coluna inteira
custa 0,0029 de AUC. As quatro primeiras features (os três contadores de atraso
e a utilização) concentram **82,07%** do ganho. Mover 10% uma coluna que
responde por 2% do modelo desloca a previsão média em 29 pontos-base, que é
exatamente o que a ablação mediu.

Vale registrar o que isso *não* significa: a inflação nominal continua sendo um
mecanismo real de drift por medição. O que a intervenção mostra é que, **neste
modelo**, ela é pequena — porque este modelo quase não usa renda. Num scorecard
que usasse renda de forma central, a mesma intervenção teria outro resultado.

---

## 5. O 2x2 da etapa 2

As duas linhas abaixo são a mesma tabela de ablação lida de outro jeito, e são
o resultado mais importante da etapa:

| braço | PSI máx (mês 6) | features 🔴 | gap de calibração | AUC |
|---|---|---|---|---|
| `composition_only` | **0.9327** | 5 | **-0.0028** | 0.8130 |
| `stress_only` | **0.0082** | 0 | **-0.0337** | 0.7917 |

**`composition_only`**: o painel de drift grita — PSI de 0.93 na
utilização, 5 features na faixa vermelha — e o modelo continua
**calibrado**, com gap de -0.0028. Alarme máximo, dano de
calibração nenhum.

**`stress_only`**: **nenhuma feature driftou** — PSI máximo de
0.0082, mais de dez vezes abaixo do limiar de alerta de 0,10, porque
os rótulos mudaram e as features não. E o gap chega a -0.0337, doze
vezes o do braço anterior. Silêncio total no painel, dano máximo.

> **Drift não é degradação, e degradação não exige drift.**

### A consequência de engenharia

Monitorar drift de features **não pode**, sozinho, pegar o mecanismo que causa
a maior parte do dano de calibração. O braço `stress_only` é invisível a PSI, a
KS por feature, a qualquer distância entre distribuições de entrada — porque
`P(X)` genuinamente não mudou. O que mudou foi `P(y|X)`, e nenhuma quantidade
de vigilância sobre `X` alcança isso.

O corolário é que **monitoramento baseado em rótulo é obrigatório**, não um
complemento: rótulos com atraso, gap de calibração, Brier por lote. É a única
família de sinal que enxerga drift de conceito. E é também a mais cara e a mais
lenta — o rótulo chega meses depois — o que faz do drift de features um sinal
*antecedente* útil e insuficiente, nunca um substituto.

Os dois juntos cobrem o quadrado inteiro; cada um sozinho cobre metade.

---

## 6. Cenário só-multivariado

Lote separado, partindo de uma amostra uniforme estilo mês 0. A dependência de
um par de colunas é invertida por **permutação**, então toda distribuição
marginal é idêntica por construção — e isso é afirmado, não presumido.

| par | Spearman ref. | no lote antes | depois | PSI máx | marginais idênticas | AUC antes | AUC depois | ΔAUC |
|---|---|---|---|---|---|---|---|---|
| `primary_by_dependence`<br>NumberOfOpenCreditLinesAndLoans / NumberRealEstateLoansOrLines | +0.4640 | +0.4538 | -0.4531 | 0.0041 | ✅ | 0.8601 | 0.8625 | **+0.0024** |
| `credit_tightening_channel`<br>RevolvingUtilizationOfUnsecuredLines / age | -0.2749 | -0.2914 | +0.2631 | 0.0041 | ✅ | 0.8601 | 0.8434 | **-0.0168** |

### Ranking de dependência no `reference`

| # | par | Spearman | Pearson |
|---|---|---|---|
| 1 | `DebtRatio` / `NumberRealEstateLoansOrLines` | +0.5369 | +0.3795 |
| 2 | `NumberOfOpenCreditLinesAndLoans` / `NumberRealEstateLoansOrLines` | +0.4640 | +0.4272 |
| 3 | `DebtRatio` / `NumberOfOpenCreditLinesAndLoans` | +0.3523 | +0.2517 |
| 4 | `MonthlyIncome` / `NumberRealEstateLoansOrLines` | +0.3514 | +0.1039 |
| 5 | `NumberOfTimes90DaysLate` / `NumberOfTime60-89DaysPastDueNotWorse` | +0.2961 | +0.2909 |
| 6 | `RevolvingUtilizationOfUnsecuredLines` / `age` | -0.2749 | -0.1841 |
| 7 | `MonthlyIncome` / `NumberOfOpenCreditLinesAndLoans` | +0.2740 | +0.0776 |
| 8 | `NumberOfTime30-59DaysPastDueNotWorse` / `NumberOfTime60-89DaysPastDueNotWorse` | +0.2664 | +0.3119 |

### Leitura

Os dois lotes provam coisas diferentes, e é por isso que os dois existem:

O par **primário**, escolhido pela regra de maior dependência entre colunas que
nenhum outro mecanismo toca, produz **resultado nulo**: PSI máximo de
0.0041 em toda feature — 🟢 com folga de duas
ordens de grandeza — e ΔAUC de
+0.0024,
dentro do ruído de ±0,007 medido no dia 4. Ou seja: **indetectável e
inofensivo**.

O par do **canal de aperto de crédito** (`utilização` / `idade`) é igualmente
indetectável — PSI máximo 0.0041 — e **não é
inofensivo**: ΔAUC de
-0.0168,
fora da banda de ruído. É o argumento mais forte da etapa: existe drift que
degrada o modelo de verdade e para o qual **todo teste univariado é cego por
construção**, porque não há nada de errado com nenhuma marginal.

A moral do par primário não é que o cenário falhou — é que "univariado não vê"
e "não importa" são duas afirmações diferentes, e testar um par só as teria
confundido. O dia 8 precisa dos dois: um mede detectabilidade, o outro mede
dano.
