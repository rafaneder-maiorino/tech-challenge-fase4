# AUCDrop

**Severidade:** warning · **Origem:** `credit_monitor_labels` · **Runbook do alerta homônimo**

## O que significa

O AUC do lote caiu para **0,8372** ou menos, abaixo do percentil 1% da
distribuição nula.

AUC mede **ordenação**: se o modelo ainda separa quem paga de quem não paga. É
mais lento a degradar que a calibração, porque a ordenação entre clientes
sobrevive a um deslocamento que move todo mundo junto.

## Primeiras verificações

### PromQL

```promql
auc <= 0.8372
brier
calibration_gap   # quase sempre rompe antes
```

### LogQL (Loki)

```logql
{job="credit-monitor", stage="labels"} | json | auc < 0.8372
```

## Causas prováveis

1. **Drift de composição forte** — a carteira mudou o suficiente para o modelo
   ordenar pior. No `composition_only` da etapa 2 o AUC cai de 0,8601 para
   0,8130 com a calibração intacta.
2. **Drift de conceito** — nesse caso o gap de calibração normalmente já
   rompeu antes.
3. **Ruído de lote.** 1% dos lotes limpos cruzam este limiar por acaso.

Vale reler o 2x2 da etapa 2 (`docs/findings.md` §9) antes de concluir
qualquer coisa: **drift não é degradação, e degradação não exige drift**. O
braço `composition_only` tem PSI de 0,93 com a calibração intacta (gap
-0,0028); o `stress_only` tem PSI máximo de 0,008 e o gap chega a -0,0337.

## Ação

1. Verificar se `CalibrationGapBreach` também disparou. Os dois juntos são um
   caso muito mais forte que qualquer um sozinho.
2. Olhar o PSI ponderado das features de maior ganho.
3. Um lote isolado: registrar. Dois seguidos: investigar.

## Quando retreinar

Só com persistência, e preferencialmente junto do gap de calibração. AUC
sozinho caindo pouco acima do ruído não paga o custo e o risco de um retreino.

## O limiar, e a taxa de falso alarme medida

**0,8372**, percentil 1% da distribuição nula do mesmo bootstrap
(500 × 7.000 linhas do holdout):

| | valor |
|---|---|
| média do nulo | 0,85871 |
| desvio do nulo | 0,00857 |
| percentil 1% | **0,8372** |
| falso alarme medido | **1,0%** |

Percentil mais frouxo que o do gap (0,5%) de propósito: este é warning e não
acorda ninguém, então pode ser mais sensível.
