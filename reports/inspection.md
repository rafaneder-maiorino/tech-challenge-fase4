# Relatório de inspeção — dataset bruto

Fonte: `data/raw/gmsc.parquet` (OpenML id 45577, *Give-Me-Some-Credit*), lido sem nenhuma limpeza, imputação ou transformação.

Este documento apenas **relata números**. Nenhuma linha aqui interpreta os valores ou propõe regra de contrato de dados.

---

## 1. Shape, dtypes e uso de memória

- Linhas: **150,000**
- Colunas: **11**
- Memória total (deep): **5.58 MB**

| Coluna | dtype | Memória (deep) |
|---|---|---|
| SeriousDlqin2yrs | category | 0.143 MB |
| RevolvingUtilizationOfUnsecuredLines | float64 | 1.144 MB |
| age | uint8 | 0.143 MB |
| NumberOfTime30-59DaysPastDueNotWorse | uint8 | 0.143 MB |
| DebtRatio | float64 | 1.144 MB |
| MonthlyIncome | float64 | 1.144 MB |
| NumberOfOpenCreditLinesAndLoans | uint8 | 0.143 MB |
| NumberOfTimes90DaysLate | uint8 | 0.143 MB |
| NumberRealEstateLoansOrLines | uint8 | 0.143 MB |
| NumberOfTime60-89DaysPastDueNotWorse | uint8 | 0.143 MB |
| NumberOfDependents | float64 | 1.144 MB |
| **(index)** | — | 0.000 MB |


---

## 2. Coluna alvo, contagem de classes e taxa de positivos

- Coluna alvo: **`SeriousDlqin2yrs`**
- dtype armazenado: `category`
- Taxa de positivos (classe 1): **6.68%**
- Razão negativos:positivos: **13.96 : 1**

| Classe | Contagem | % do total |
|---|---|---|
| 0 | 139,974 | 93.32% |
| 1 | 10,026 | 6.68% |


---

## 3. Valores ausentes

| Coluna | Ausentes | % ausentes |
|---|---|---|
| SeriousDlqin2yrs | 0 | 0.00% |
| RevolvingUtilizationOfUnsecuredLines | 0 | 0.00% |
| age | 0 | 0.00% |
| NumberOfTime30-59DaysPastDueNotWorse | 0 | 0.00% |
| DebtRatio | 0 | 0.00% |
| MonthlyIncome | 29,731 | 19.82% |
| NumberOfOpenCreditLinesAndLoans | 0 | 0.00% |
| NumberOfTimes90DaysLate | 0 | 0.00% |
| NumberRealEstateLoansOrLines | 0 | 0.00% |
| NumberOfTime60-89DaysPastDueNotWorse | 0 | 0.00% |
| NumberOfDependents | 3,924 | 2.62% |

- Linhas com **pelo menos um** valor ausente: **29,731** (19.82%)
- Linhas completas: **120,269** (80.18%)


---

## 4. Estatísticas descritivas das colunas numéricas

Percentis incluídos: 1%, 5%, 25%, 50%, 75%, 95%, 99%. `SeriousDlqin2yrs` aparece aqui convertida de `category` para inteiro (ver seção 2); o arquivo em disco não foi alterado.

| Coluna | count | mean | std | min | 1% | 5% | 25% | 50% | 75% | 95% | 99% | max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SeriousDlqin2yrs | 150,000.0000 | 0.0668 | 0.2497 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 1.0000 | 1.0000 |
| RevolvingUtilizationOfUnsecuredLines | 150,000.0000 | 6.0484 | 249.7554 | 0.0000 | 0.0000 | 0.0000 | 0.0299 | 0.1542 | 0.5590 | 1.0000 | 1.0930 | 50,708.0000 |
| age | 150,000.0000 | 52.2952 | 14.7719 | 0.0000 | 24.0000 | 29.0000 | 41.0000 | 52.0000 | 63.0000 | 78.0000 | 87.0000 | 109.0000 |
| NumberOfTime30-59DaysPastDueNotWorse | 150,000.0000 | 0.4210 | 4.1928 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 2.0000 | 4.0000 | 98.0000 |
| DebtRatio | 150,000.0000 | 353.0051 | 2,037.8185 | 0.0000 | 0.0000 | 0.0043 | 0.1751 | 0.3665 | 0.8683 | 2,449.0000 | 4,979.0400 | 329,664.0000 |
| MonthlyIncome | 120,269.0000 | 6,670.2212 | 14,384.6742 | 0.0000 | 0.0000 | 1,300.0000 | 3,400.0000 | 5,400.0000 | 8,249.0000 | 14,587.6000 | 25,000.0000 | 3,008,750.0000 |
| NumberOfOpenCreditLinesAndLoans | 150,000.0000 | 8.4528 | 5.1460 | 0.0000 | 0.0000 | 2.0000 | 5.0000 | 8.0000 | 11.0000 | 18.0000 | 24.0000 | 58.0000 |
| NumberOfTimes90DaysLate | 150,000.0000 | 0.2660 | 4.1693 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 3.0000 | 98.0000 |
| NumberRealEstateLoansOrLines | 150,000.0000 | 1.0182 | 1.1298 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 2.0000 | 3.0000 | 4.0000 | 54.0000 |
| NumberOfTime60-89DaysPastDueNotWorse | 150,000.0000 | 0.2404 | 4.1552 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 2.0000 | 98.0000 |
| NumberOfDependents | 146,076.0000 | 0.7572 | 1.1151 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 3.0000 | 4.0000 | 20.0000 |


---

## 5. Idade: valores implausíveis

| Condição | Contagem | % do total |
|---|---|---|
| age < 18 | 1 | 0.00% |
| age == 0 | 1 | 0.00% |
| age > 100 | 13 | 0.01% |

- `age` mínima: **0**
- `age` máxima: **109**


---

## 6. Contadores de inadimplência: valores >= 90

`value_counts` completo dos valores altos de cada contador, com a taxa de inadimplência (`SeriousDlqin2yrs` = 1) das linhas que os carregam.

### `NumberOfTime30-59DaysPastDueNotWorse`

| Valor | Contagem | % do total | Taxa de inadimplência |
|---|---|---|---|
| 96 | 5 | 0.00% | 80.00% |
| 98 | 264 | 0.18% | 54.17% |

- Agregado (valores >= 90): **269** linhas (0.18%), taxa de inadimplência **54.65%**
- Linhas com valor < 90: taxa de inadimplência **6.60%**
- Valor máximo abaixo do corte: **13**

### `NumberOfTimes90DaysLate`

| Valor | Contagem | % do total | Taxa de inadimplência |
|---|---|---|---|
| 96 | 5 | 0.00% | 80.00% |
| 98 | 264 | 0.18% | 54.17% |

- Agregado (valores >= 90): **269** linhas (0.18%), taxa de inadimplência **54.65%**
- Linhas com valor < 90: taxa de inadimplência **6.60%**
- Valor máximo abaixo do corte: **17**

### `NumberOfTime60-89DaysPastDueNotWorse`

| Valor | Contagem | % do total | Taxa de inadimplência |
|---|---|---|---|
| 96 | 5 | 0.00% | 80.00% |
| 98 | 264 | 0.18% | 54.17% |

- Agregado (valores >= 90): **269** linhas (0.18%), taxa de inadimplência **54.65%**
- Linhas com valor < 90: taxa de inadimplência **6.60%**
- Valor máximo abaixo do corte: **11**


---

## 7. Linhas duplicadas exatas

- Duplicatas considerando **todas as 11 colunas**: **609** (0.41%)
- Nenhuma coluna de identificador foi encontrada neste dataset (procurado por: id, index, row_id, rowid, unnamed: 0). A contagem ignorando ID é, portanto, idêntica à de cima: **609**.
- Linhas distintas: **149,391**


---

## 8. `RevolvingUtilizationOfUnsecuredLines` acima de 1 e de 10

| Condição | Contagem | % do total |
|---|---|---|
| RevolvingUtilizationOfUnsecuredLines > 1 | 3,321 | 2.21% |
| RevolvingUtilizationOfUnsecuredLines > 10 | 241 | 0.16% |

- Valor máximo observado: **50,708.00**


---

## 9. `DebtRatio` por presença de `MonthlyIncome`

### Estatísticas descritivas de `DebtRatio`

| Grupo | count | mean | std | min | 1% | 5% | 25% | 50% | 75% | 95% | 99% | max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MonthlyIncome ausente | 29,731.0000 | 1,673.3966 | 4,248.3729 | 0.0000 | 0.0000 | 0.0000 | 123.0000 | 1,159.0000 | 2,382.0000 | 4,902.5000 | 8,084.5000 | 329,664.0000 |
| MonthlyIncome presente | 120,269.0000 | 26.5988 | 424.4465 | 0.0000 | 0.0000 | 0.0045 | 0.1434 | 0.2960 | 0.4826 | 1.1288 | 661.8200 | 61,106.5000 |

### Tamanho do grupo e taxa de inadimplência

| Grupo | Linhas | % do total | Taxa de inadimplência |
|---|---|---|---|
| MonthlyIncome ausente | 29,731 | 19.82% | 5.61% |
| MonthlyIncome presente | 120,269 | 80.18% | 6.95% |


---

## 10. Matriz de correlação de Pearson

Colunas numéricas, duas casas decimais. Pares com valor ausente são descartados par a par (comportamento padrão de `DataFrame.corr`), de modo que as correlações envolvendo `MonthlyIncome` e `NumberOfDependents` usam menos linhas que as demais.

|  | SeriousDlqin2yrs | RevolvingUtilizationOfUnsecuredLines | age | NumberOfTime30-59DaysPastDueNotWorse | DebtRatio | MonthlyIncome | NumberOfOpenCreditLinesAndLoans | NumberOfTimes90DaysLate | NumberRealEstateLoansOrLines | NumberOfTime60-89DaysPastDueNotWorse | NumberOfDependents |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SeriousDlqin2yrs | 1.00 | -0.00 | -0.12 | 0.13 | -0.01 | -0.02 | -0.03 | 0.12 | -0.01 | 0.10 | 0.05 |
| RevolvingUtilizationOfUnsecuredLines | -0.00 | 1.00 | -0.01 | -0.00 | 0.00 | 0.01 | -0.01 | -0.00 | 0.01 | -0.00 | 0.00 |
| age | -0.12 | -0.01 | 1.00 | -0.06 | 0.02 | 0.04 | 0.15 | -0.06 | 0.03 | -0.06 | -0.21 |
| NumberOfTime30-59DaysPastDueNotWorse | 0.13 | -0.00 | -0.06 | 1.00 | -0.01 | -0.01 | -0.06 | 0.98 | -0.03 | 0.99 | -0.00 |
| DebtRatio | -0.01 | 0.00 | 0.02 | -0.01 | 1.00 | -0.03 | 0.05 | -0.01 | 0.12 | -0.01 | -0.04 |
| MonthlyIncome | -0.02 | 0.01 | 0.04 | -0.01 | -0.03 | 1.00 | 0.09 | -0.01 | 0.12 | -0.01 | 0.06 |
| NumberOfOpenCreditLinesAndLoans | -0.03 | -0.01 | 0.15 | -0.06 | 0.05 | 0.09 | 1.00 | -0.08 | 0.43 | -0.07 | 0.07 |
| NumberOfTimes90DaysLate | 0.12 | -0.00 | -0.06 | 0.98 | -0.01 | -0.01 | -0.08 | 1.00 | -0.05 | 0.99 | -0.01 |
| NumberRealEstateLoansOrLines | -0.01 | 0.01 | 0.03 | -0.03 | 0.12 | 0.12 | 0.43 | -0.05 | 1.00 | -0.04 | 0.12 |
| NumberOfTime60-89DaysPastDueNotWorse | 0.10 | -0.00 | -0.06 | 0.99 | -0.01 | -0.01 | -0.07 | 0.99 | -0.04 | 1.00 | -0.01 |
| NumberOfDependents | 0.05 | 0.00 | -0.21 | -0.00 | -0.04 | 0.06 | 0.07 | -0.01 | 0.12 | -0.01 | 1.00 |
