# CalibrationGapBreach

**Severidade:** critical · **Origem:** `credit_monitor_labels` · **Runbook do alerta homônimo**

## O que significa

O **gap de calibração** — previsto médio menos observado — passou de
**-0,0056**, além do percentil 0,5% da distribuição nula.

Este é **o sinal de degradação silenciosa**. É a única métrica da stack que
enxerga drift de conceito: o `stress_only` da etapa 2 muda `P(y|X)` sem mover
uma única feature, e nenhum alerta de drift dispara enquanto o gap chega a
-0,0337.

E ele chega **atrasado**, por construção: o rótulo leva `label_lag_months`
(2) para existir.

## Primeiras verificações

### PromQL

```promql
calibration_gap <= -0.0056
auc
observed_default_rate    # a realidade
prediction_mean          # o que o modelo achava

# quantos lotes ainda não têm rótulo — a janela cega
labels_pending_batches
```

### LogQL (Loki)

```logql
{job="credit-monitor", stage="labels"} | json | calibration_gap < -0.0056
```

## Causas prováveis

1. **Drift de conceito** — `P(y|X)` mudou. Recessão, mudança regulatória,
   política de crédito nova. As features podem estar perfeitamente estáveis.
2. **Drift de medição** — uma feature passou a ser medida de outro jeito e o
   modelo lê risco menor do que existe. Foi o mecanismo de inflação da etapa 2
   (embora lá o efeito tenha sido pequeno: a renda vale 2,15% do ganho).
3. **Mudança de população que o modelo não cobre** — clientes fora do domínio
   de treino.

Vale reler o 2x2 da etapa 2 (`docs/findings.md` §9) antes de concluir
qualquer coisa: **drift não é degradação, e degradação não exige drift**. O
braço `composition_only` tem PSI de 0,93 com a calibração intacta (gap
-0,0028); o `stress_only` tem PSI máximo de 0,008 e o gap chega a -0,0337.

## Ação

1. Olhar `observed_default_rate` contra `prediction_mean`: o modelo está
   subestimando ou superestimando?
2. Checar se **algum** alerta de drift de feature disparou para o mesmo lote. Se
   não: é drift de conceito puro, e nenhuma vigilância sobre `X` teria pegado.
3. Confirmar que não é lote pequeno (`sample_sufficient`).
4. Escalar. Este é o alerta que acorda alguém.

## Quando retreinar

**Sim, este é o alerta que justifica retreino** — com duas condições:

1. **Persistência.** Um lote isolado além do limiar tem 0,6% de chance de ser
   falso alarme; dois lotes seguidos já não.
2. **Rótulo suficiente.** Retreinar precisa do desfecho, e o desfecho tem
   atraso. Retreinar com os últimos dois meses não é possível: eles ainda não
   têm rótulo.

Retreinar no `reference` mais os meses já rotulados, e **revalidar contra o
`ModelInputSchema`** antes de promover. O alias `champion` é o ponto de
promoção: `models:/credit-default-baseline@champion`.

## O limiar, e a taxa de falso alarme medida

**-0,0056**, calibrado no dia 10 pelo mesmo método do dia 8: 500 sorteios de
7.000 linhas do holdout, sem drift nenhum, e o limiar sai da distribuição nula.

| | valor |
|---|---|
| média do nulo | +0,00075 |
| desvio do nulo | 0,00241 |
| percentil 0,5% | **-0,0056** |
| falso alarme medido | **0,6%** |

O limiar provisório de -0,015 tinha falso alarme de **0,0%** — seguro e cego:
ficava 6,5 desvios abaixo da média nula, então só disparava para degradação
enorme. Trocá-lo mudou a janela cega do cenário `full` de +1 para -1 mês
(`docs/findings.md` §13).
