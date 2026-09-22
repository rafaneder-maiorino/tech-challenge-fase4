# FeatureDrift

**Severidade:** warning / critical · **Origem:** `credit_monitor_scoring` · **Runbook do alerta homônimo**

## O que significa

O PSI de pelo menos uma feature passou de **0,10** (warning) contra a
referência da etapa 1.

O **critical** exige duas coisas ao mesmo tempo: PSI acima de **0,25** **e** a
feature carregando **10% ou mais** do ganho do campeão. Uma feature com PSI alto
e pouco peso continua em warning — é o princípio PSI × ganho do dia 7 e a
ressalva de ordenação por dano do MMD do dia 8: **um detector mede tamanho
estatístico, não prejuízo**.

Exemplo real, mês 6 do cenário `full`: `age` tem PSI 0,314 e 3,29% do ganho;
`NumberOfTimes90DaysLate` tem PSI 0,302 e **28,47%**. A segunda é crítica e a
primeira não, apesar de driftar menos.

## Primeiras verificações

### PromQL

```promql
# o que está driftando, ordenado pelo que importa
sort_desc(drift_psi_weighted)

# o PSI cru, para comparar
sort_desc(drift_psi)

# quantas features em cada faixa
drifted_features

# o peso da feature no modelo
feature_gain_share
```

### LogQL (Loki)

```logql
{job="credit-monitor", stage="drift"} | json | drifted_critical > 0
```

## Causas prováveis

1. **A população mudou** — composição da carteira, sazonalidade, uma campanha
   de aquisição nova. É drift de dados puro, `P(X)`.
2. **Mudança a montante** que o contrato não pega: nova regra de arredondamento,
   fonte de dado trocada, unidade diferente.
3. **Drift de conceito junto** — nesse caso o gap de calibração também vai
   disparar, com o atraso do rótulo.

Vale reler o 2x2 da etapa 2 (`docs/findings.md` §9) antes de concluir
qualquer coisa: **drift não é degradação, e degradação não exige drift**. O
braço `composition_only` tem PSI de 0,93 com a calibração intacta (gap
-0,0028); o `stress_only` tem PSI máximo de 0,008 e o gap chega a -0,0337.

## Ação

1. Abrir a dashboard **Feature** (`cm-feature`) e olhar a feature pelo PSI
   ponderado, não pelo PSI cru.
2. Se o PSI ponderado for baixo, **registrar e seguir**: a feature driftou e o
   modelo quase não a usa.
3. Se for alto, comparar com o gap de calibração do mesmo mês — lembrando que
   ele só existe `label_lag_months` depois.
4. Checar `reports/drift_tests/summary.md` para o cross-check de PSI, caso o
   número do Evidently e o nosso discordem (achado §10).

## Quando retreinar

**Só com evidência de dano.** Drift de feature sozinho não é motivo: o braço
`composition_only` tem PSI de 0,93 e mantém a calibração intacta — o modelo não
está errado, a população é que é outra.

Retreinar quando **o gap de calibração também romper** (ver
`CalibrationGapBreach.md`), ou quando o PSI ponderado das features de maior
ganho ficar persistentemente crítico por vários lotes.

## O limiar, e a taxa de falso alarme medida

0,10 e 0,25, do teste A/A da etapa 2 (`docs/findings.md` §11).

No tamanho de lote de produção (~7.000 linhas), em **200 sorteios de uma
população sem drift nenhum**:

| regra | falso alarme medido |
|---|---|
| PSI > 0,10 | **0,0%** |
| PSI > 0,25 | **0,0%** |

Zero em 200 sorteios limita a taxa real a menos de **1,5% com 95% de
confiança**. Para comparação, o KS sem correção dá **21,5%** no mesmo tamanho —
é por isso que ele é diagnóstico e não decide veredito.
