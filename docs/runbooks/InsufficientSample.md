# InsufficientSample

**Severidade:** info · **Origem:** `credit_monitor_scoring` · **Runbook do alerta homônimo**

## O que significa

O lote tem menos de **1.000 linhas** e o monitor **absteve-se**:
`drift_verdict = -1`, `sample_sufficient = 0`.

Isto **não é verde**. É ausência de veredito. Um lote pequeno não torna o drift
menos provável — torna a medição incapaz de separar drift do próprio viés.

## Primeiras verificações

### PromQL

```promql
sample_sufficient == 0
batch_size < 1000

# o veredito correspondente: -1, nem verde nem vermelho
drift_verdict == -1
```

### LogQL (Loki)

```logql
{job="credit-monitor"} |= "drift.insufficient_sample" | json
```

## Causas prováveis

1. **Lote parcial.** A extração a montante entregou menos do que o normal —
   janela de tempo menor, filtro a mais, job interrompido.
2. **Lote de demonstração.** O `small_demo` (400 linhas) existe justamente para
   exercitar este estado de ponta a ponta.
3. **Queda real de volume.** Menos clientes entrando é um fato de negócio, e
   nesse caso o alerta está certo e a conversa não é técnica.

## Ação

1. Confirmar se o volume caiu ou se o lote chegou truncado.
2. Se truncado: reprocessar depois de corrigir a origem.
3. Se o volume caiu de verdade: **agregar lotes** até cruzar as 1.000 linhas
   antes de pedir um veredito. Somar dois meses é preferível a ler um número
   que não significa nada.
4. Nunca baixar o `min_batch_size` para "ver um veredito". O piso foi medido,
   não escolhido.

## Quando retreinar

Não. O alerta é sobre volume de dado, não sobre o modelo.

## O limiar, e a taxa de falso alarme medida

`min_batch_size = 1000`, de `configs/monitoring.yaml`, medido no teste A/A da
etapa 2 (`docs/findings.md` §11).

A n = 250 o **viés de pequena amostra do PSI atinge o limiar de alerta**:
`NumberRealEstateLoansOrLines` tem 26 bins e viés previsto de 0,1002 contra um
limiar de 0,10. Na prática, **28,5%** dos lotes sem drift algum disparavam
PSI > 0,10 naquele tamanho. A 500 a taxa cai para 1,0%, e **1.000 é o primeiro
tamanho com 0,0% medido** em 200 sorteios.
