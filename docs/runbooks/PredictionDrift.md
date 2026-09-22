# PredictionDrift

**Severidade:** warning · **Origem:** `credit_monitor_scoring` · **Runbook do alerta homônimo**

## O que significa

O PSI da distribuição de **probabilidades previstas** passou de 0,10 contra a
distribuição de score da referência.

É o sinal mais precoce disponível **sem rótulo**: agrega todas as features de
uma vez e existe no instante da pontuação. Quando ele se move e nenhuma feature
individual se move, a mudança está na **combinação** — exatamente o que o teste
MMD da etapa 2 detecta e nenhum teste univariado vê.

## Primeiras verificações

### PromQL

```promql
prediction_psi > 0.10
prediction_mean          # para onde o score foi
sort_desc(drift_psi)     # alguma feature explica?
```

### LogQL (Loki)

```logql
{job="credit-monitor", stage="drift"} | json | prediction_mean > 0
```

## Causas prováveis

1. **Várias features driftando um pouco**, somando no score.
2. **Mudança de dependência entre features** sem mudança de marginal — o caso
   que a etapa 2 construiu de propósito e mediu: PSI univariado de 0,0041 em
   todas as features, e o MMD detecta.
3. **A população realmente ficou mais arriscada** — e então o observado vai
   acompanhar, quando o rótulo chegar.

## Ação

1. Comparar com `prediction_mean`: subiu ou desceu? Subiu significa que o
   modelo está lendo mais risco.
2. Se nenhuma feature individual explica, rodar `make mmd` — é o teste que vê
   dependência.
3. Esperar o rótulo antes de concluir sobre dano. Score mudar não é modelo
   errar.

## Quando retreinar

Não isoladamente. É um sinal de investigação, não de ação.

## O limiar, e a taxa de falso alarme medida

Mesmo 0,10 do PSI de feature, pela mesma calibração A/A. O score é uma coluna
contínua como as outras e a distribuição nula do PSI depende do número de bins
e do tamanho do lote, não da semântica da coluna.
