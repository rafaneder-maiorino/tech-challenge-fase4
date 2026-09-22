# LabelsPending

**Severidade:** info · **Origem:** `credit_monitor_scoring` · **Runbook do alerta homônimo**

## O que significa

Há lotes já pontuados cujo **desfecho ainda não chegou**.

Durante essa janela **nenhum sinal de saúde do modelo existe** para eles. Só há
drift de feature — que, como a etapa 2 mediu, não vê drift de conceito.

Este alerta existe para tornar a **janela cega** visível na tela em vez de
implícita numa ausência de dado. Um painel de saúde vazio parece "tudo bem"; o
que ele quer dizer é "ainda não sei".

## Primeiras verificações

### PromQL

```promql
labels_pending_batches
labels_pending == 1      # quais lotes

# o drift que existe para esses lotes, e é tudo que existe
drift_verdict
```

### LogQL (Loki)

```logql
{job="credit-monitor"} |= "scoring.finished" | json
```

## Causas prováveis

1. **Normal.** Com `label_lag_months = 2`, os dois últimos lotes estão sempre
   pendentes. O alerta é informativo, não um defeito.
2. **Anormal** se o número cresce sem parar: a entrega de rótulo parou.

## Ação

1. Se o número for igual ao `label_lag_months`, está tudo no esperado.
2. Se for maior e crescendo, o problema é a origem do rótulo, não o modelo.
3. Durante a janela, a única defesa é o drift de feature — e vale lembrar
   explicitamente que ele não cobre drift de conceito.

## Quando retreinar

Não. Mas vale registrar: **encurtar o atraso de rótulo é a única alavanca que
reduz a janela cega para drift de conceito puro**. Nenhum limiar e nenhuma
feature adicional encurtam — só receber o desfecho mais cedo.

## O limiar, e a taxa de falso alarme medida

Qualquer valor acima de zero. `label_lag_months = 2`, de
`configs/monitoring.yaml`.

Janela cega medida (`docs/findings.md` §13), com limiar calibrado:

| cenário | lead time |
|---|---|
| `full` | **-1 mês** |
| `composition_only` | n/a (nunca degrada) |
| `stress_only` | **-2 meses** |
