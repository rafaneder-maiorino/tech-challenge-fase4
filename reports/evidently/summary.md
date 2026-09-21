# Relatórios de drift — resumo

Gerados por `make drift-reports` com **`evidently==0.7.23`** em 2026-09-21.
Referência: 103,152 linhas. A versão, a escolha de API e o porquê de
cada configuração estão em [`docs/evidently.md`](../../docs/evidently.md).

Nenhuma métrica foi deixada por omissão: **PSI em todas as 11 features**
(método `reference_psi`, limiares 0,10 / 0,25), **KS nas 4 contínuas**, e PSI no
score previsto. 🟢 estável · 🟡 moderado · 🔴 significativo.

---

## 1. Cross-check: o nosso PSI contra o nativo do Evidently

Antes de confiar em qualquer número do monitor, os dois estimadores foram
postos lado a lado no mês 6. A divergência é real e tem causa conhecida —
ver `docs/findings.md` §10.

| feature | nosso PSI (quantis do reference) | PSI nativo do Evidently (Sturges) | razão | mesmo veredito |
|---|---|---|---|---|
| `RevolvingUtilizationOfUnsecuredLines` | 0.9420 | 0.6486 | 1.45x | ✅ |
| `age` | 0.3144 | 0.3179 | 0.99x | ✅ |
| `NumberOfTime30-59DaysPastDueNotWorse` | 0.4418 | 0.4441 | 0.99x | ✅ |
| `DebtRatio` | 0.0244 | 0.1083 | 0.23x | ❌ |
| `MonthlyIncome` | 0.0218 | 0.0159 | 1.37x | ✅ |
| `NumberOfOpenCreditLinesAndLoans` | 0.0849 | 0.0769 | 1.10x | ✅ |
| `NumberOfTimes90DaysLate` | 0.3018 | 0.3019 | 1.00x | ✅ |
| `NumberRealEstateLoansOrLines` | 0.0441 | 0.0132 | 3.34x | ✅ |
| `NumberOfTime60-89DaysPastDueNotWorse` | 0.2775 | 0.2796 | 0.99x | ✅ |
| `NumberOfDependents` | 0.0201 | 0.0205 | 0.98x | ✅ |
| `income_missing` | 0.0134 | 0.0134 | 1.00x | ✅ |

Nas colunas de contagem os dois concordam em três casas decimais: o Evidently
também troca para **um bin por valor** quando a coluna tem 20 ou menos valores
distintos, então **não** tem o bug que o dia 6 encontrou no nosso.

Nas contínuas ele usa bins de **largura igual** sobre a união de referência e
lote, e aí diverge — inclusive trocando o veredito de uma feature. O relatório
usa o `reference_psi` registrado, com bins de quantil cortados no `reference`.

## 2. PSI por feature e por mês

Ordenado por participação no ganho do campeão, que é a coluna que diz se um
drift importa.

| feature | ganho | mês 0 | mês 1 | mês 2 | mês 3 | mês 4 | mês 5 | mês 6 |
|---|---|---|---|---|---|---|---|---|
| `NumberOfTimes90DaysLate` | 28.47% | 0.003 🟢 | 0.006 🟢 | 0.017 🟢 | 0.036 🟢 | 0.116 🟡 | 0.214 🟡 | 0.302 🔴 |
| `NumberOfTime30-59DaysPastDueNotWorse` | 22.17% | 0.004 🟢 | 0.013 🟢 | 0.055 🟢 | 0.111 🟡 | 0.217 🟡 | 0.329 🔴 | 0.442 🔴 |
| `RevolvingUtilizationOfUnsecuredLines` | 18.89% | 0.001 🟢 | 0.039 🟢 | 0.112 🟡 | 0.250 🟡 | 0.500 🔴 | 0.694 🔴 | 0.942 🔴 |
| `NumberOfTime60-89DaysPastDueNotWorse` | 12.54% | 0.001 🟢 | 0.004 🟢 | 0.016 🟢 | 0.041 🟢 | 0.117 🟡 | 0.201 🟡 | 0.278 🔴 |
| `NumberRealEstateLoansOrLines` | 3.98% | 0.003 🟢 | 0.005 🟢 | 0.009 🟢 | 0.011 🟢 | 0.021 🟢 | 0.027 🟢 | 0.044 🟢 |
| `age` | 3.29% | 0.001 🟢 | 0.013 🟢 | 0.052 🟢 | 0.101 🟡 | 0.189 🟡 | 0.251 🔴 | 0.314 🔴 |
| `DebtRatio` | 2.56% | 0.001 🟢 | 0.002 🟢 | 0.005 🟢 | 0.008 🟢 | 0.015 🟢 | 0.019 🟢 | 0.024 🟢 |
| `NumberOfOpenCreditLinesAndLoans` | 2.43% | 0.001 🟢 | 0.005 🟢 | 0.007 🟢 | 0.017 🟢 | 0.042 🟢 | 0.052 🟢 | 0.085 🟢 |
| `MonthlyIncome` | 2.15% | 0.002 🟢 | 0.002 🟢 | 0.004 🟢 | 0.012 🟢 | 0.012 🟢 | 0.017 🟢 | 0.022 🟢 |
| `income_missing` | 1.80% | 0.000 🟢 | 0.000 🟢 | 0.001 🟢 | 0.004 🟢 | 0.007 🟢 | 0.011 🟢 | 0.013 🟢 |
| `NumberOfDependents` | 1.72% | 0.001 🟢 | 0.005 🟢 | 0.006 🟢 | 0.011 🟢 | 0.018 🟢 | 0.019 🟢 | 0.020 🟢 |

## 3. KS nas features contínuas — mês 6

| feature | estatística KS | p-valor | PSI | veredito (por PSI) |
|---|---|---|---|---|
| `RevolvingUtilizationOfUnsecuredLines` | 0.3993 | < 1e-300 | 0.9420 | significativo |
| `age` | 0.2150 | 1.845e-279 | 0.3144 | significativo |
| `DebtRatio` | 0.0370 | 1.236e-08 | 0.0244 | estável |
| `MonthlyIncome` | 0.0576 | 2.772e-20 | 0.0218 | estável |

Os p-valores estão **registrados e não são usados para veredito**. Com ~7,4 mil
linhas contra 103,152, o KS tem poder para declarar significante
qualquer diferença. Quanto disso é falso alarme é o que o dia 8 mede.

## 4. Ranking por PSI x ganho

Um drift de 0,30 numa feature que carrega 28% do ganho e o mesmo 0,30 numa que
carrega 2% não são o mesmo evento. Uma tabela ordenada só por PSI os apresenta
como se fossem.

### Mês 3

| # | feature | PSI | ganho | **PSI x ganho** | veredito |
|---|---|---|---|---|---|
| 1 | `RevolvingUtilizationOfUnsecuredLines` | 0.250 | 18.89% | **0.0472** | moderado |
| 2 | `NumberOfTime30-59DaysPastDueNotWorse` | 0.111 | 22.17% | **0.0247** | moderado |
| 3 | `NumberOfTimes90DaysLate` | 0.036 | 28.47% | **0.0104** | estável |
| 4 | `NumberOfTime60-89DaysPastDueNotWorse` | 0.041 | 12.54% | **0.0051** | estável |
| 5 | `age` | 0.101 | 3.29% | **0.0033** | moderado |
| 6 | `NumberRealEstateLoansOrLines` | 0.011 | 3.98% | **0.0004** | estável |

### Mês 6

| # | feature | PSI | ganho | **PSI x ganho** | veredito |
|---|---|---|---|---|---|
| 1 | `RevolvingUtilizationOfUnsecuredLines` | 0.942 | 18.89% | **0.1779** | significativo |
| 2 | `NumberOfTime30-59DaysPastDueNotWorse` | 0.442 | 22.17% | **0.0979** | significativo |
| 3 | `NumberOfTimes90DaysLate` | 0.302 | 28.47% | **0.0859** | significativo |
| 4 | `NumberOfTime60-89DaysPastDueNotWorse` | 0.278 | 12.54% | **0.0348** | significativo |
| 5 | `age` | 0.314 | 3.29% | **0.0104** | significativo |
| 6 | `NumberOfOpenCreditLinesAndLoans` | 0.085 | 2.43% | **0.0021** | estável |

## 5. O 2x2, como dois arquivos

O braço `stress_only` no mês 6: os rótulos mudaram, **nenhuma feature
mudou**.

| relatório | o que mostra |
|---|---|
| `stress_only_scoring_month_06.html` | 🟢 PSI máximo de **0.0082** (`NumberRealEstateLoansOrLines`), todas as features estáveis |
| `stress_only_label_month_06.html` | 🔴 qualidade de classificação degradada, drift de alvo |

O mesmo lote, o mesmo mês, dois vereditos opostos — porque um dos relatórios
pode ver o rótulo e o outro não. É a razão pela qual monitoramento baseado em
rótulo não é complementar, é obrigatório.

## 6. Arquivos versionados

Todos abrem offline: o JavaScript vai embutido e nenhuma CDN é consultada
(verificado — zero tags `<script src=...>` ou `<link href=...>` apontando para
fora). O tamanho é o preço disso.

| arquivo | tamanho |
|---|---|
| `scoring_month_00.html` | 4.03 MB |
| `scoring_month_03.html` | 4.03 MB |
| `scoring_month_06.html` | 4.03 MB |
| `label_month_06.html` | 3.53 MB |
| `stress_only_scoring_month_06.html` | 4.03 MB |
| `stress_only_label_month_06.html` | 3.54 MB |

O restante (`scoring_month_01/02/04/05`, `label_month_00..05`) é regenerável
por `make drift-reports` e fica fora do repositório.
