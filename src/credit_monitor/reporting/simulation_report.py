"""Render the simulation run as a markdown report.

One artefact, four tables, and the frozen parameters that produced them. The
report is the thing a reviewer reads instead of rerunning the simulation, so it
has to carry enough to be checkable: the parameters, the calibration target and
whether it was met, and the numbers that refute part of the design as well as
the numbers that confirm it.
"""

from pathlib import Path
from typing import Any

from credit_monitor.simulation.config import SimulationConfig
from credit_monitor.simulation.psi import ALERT_THRESHOLD, WARN_THRESHOLD
from credit_monitor.simulation.simulate import MultivariateResult, SimulationResult

# Rank-robust evidence for how little the champion leans on income, measured
# once and quoted here because the numbers come from a separate analysis
# (permutation importance and XGBoost gain over the reference set) rather than
# from the simulation run.
INCOME_EVIDENCE_TABLE: str = "\n".join(
    [
        "| evidência | `MonthlyIncome` | topo da lista | posição |",
        "|---|---|---|---|",
        "| importância por permutação (queda de AUC) | **+0,0029** "
        "| utilização, +0,0757 | 7 de 11 |",
        "| ganho do XGBoost | **2,15%** | 90d+, 28,47% | 9 de 11 |",
        "| AUC univariada (discriminação) | 0,5746 | utilização, 0,7770 | 6 de 11 |",
    ]
)


def _band(value: float) -> str:
    """Traffic light for a PSI value, matching the conventional reading."""
    if value < WARN_THRESHOLD:
        return "🟢"
    if value < ALERT_THRESHOLD:
        return "🟡"
    return "🔴"


def psi_ramp_table(result: SimulationResult) -> str:
    """PSI per feature per month, with the traffic light next to each cell."""
    frame = result.psi_frame()
    header = "| mês | " + " | ".join(frame.columns) + " |"
    divider = "|---" * (len(frame.columns) + 1) + "|"
    rows = []
    for month in frame.index:
        cells = " | ".join(
            f"{frame.loc[month, column]:.3f} {_band(frame.loc[month, column])}"
            for column in frame.columns
        )
        rows.append(f"| **{month}** | {cells} |")
    return "\n".join([header, divider, *rows])


def calibration_check(result: SimulationResult, n_months: int) -> str:
    """Whether the four frozen calibration targets were met."""
    frame = result.psi_frame()
    checks = [
        ("mês 0: toda feature < 0,10", bool((frame.loc[0] < WARN_THRESHOLD).all())),
        ("mês 1: toda feature < 0,10", bool((frame.loc[1] < WARN_THRESHOLD).all())),
        (
            "mês 3: ao menos duas features em [0,10; 0,25]",
            int(
                (
                    (frame.loc[3] >= WARN_THRESHOLD) & (frame.loc[3] <= ALERT_THRESHOLD)
                ).sum()
            )
            >= 2,
        ),
        (
            f"mês {n_months}: ao menos duas features > 0,25",
            int((frame.loc[n_months] > ALERT_THRESHOLD).sum()) >= 2,
        ),
    ]
    lines = ["| alvo | atingido |", "|---|---|"]
    lines += [f"| {name} | {'✅' if ok else '❌'} |" for name, ok in checks]
    return "\n".join(lines)


def metrics_table(result: SimulationResult) -> str:
    """Per-month metrics, calibration gap last because it is the headline."""
    frame = result.metrics_frame()
    lines = [
        "| mês | linhas | AUC-ROC | KS | Brier | inadimplência observada "
        "| previsto médio | **gap de calibração** |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in frame.itertuples():
        lines.append(
            f"| {row.month} | {row.rows:,} | {row.auc_roc:.4f} | "
            f"{row.ks_statistic:.4f} | {row.brier:.4f} | "
            f"{row.observed_default_rate:.2%} | {row.mean_predicted:.2%} | "
            f"**{row.calibration_gap:+.4f}** |"
        )
    return "\n".join(lines)


def ablation_table(arms: list[SimulationResult], metric: str, fmt: str) -> str:
    """One metric across configurations and months."""
    months = [month.month for month in arms[0].months]
    lines = [
        "| configuração | " + " | ".join(f"mês {m}" for m in months) + " |",
        "|---" * (len(months) + 1) + "|",
    ]
    for arm in arms:
        cells = " | ".join(
            format(getattr(month.metrics, metric), fmt) for month in arm.months
        )
        lines.append(f"| `{arm.label}` | {cells} |")
    return "\n".join(lines)


def multivariate_section(results: list[MultivariateResult]) -> str:
    """Both dependence-only batches, side by side."""
    lines = [
        "| par | Spearman ref. | no lote antes | depois | PSI máx | marginais "
        "idênticas | AUC antes | AUC depois | ΔAUC |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        dependence = result.dependence
        delta = result.metrics_after.auc_roc - result.metrics_before.auc_roc
        lines.append(
            f"| `{result.label}`<br>{dependence.column_x} / {dependence.column_y} "
            f"| {dependence.reference_spearman:+.4f} "
            f"| {dependence.before_spearman:+.4f} "
            f"| {dependence.after_spearman:+.4f} "
            f"| {result.max_psi_after:.4f} "
            f"| {'✅' if result.marginals_preserved else '❌'} "
            f"| {result.metrics_before.auc_roc:.4f} "
            f"| {result.metrics_after.auc_roc:.4f} "
            f"| **{delta:+.4f}** |"
        )
    return "\n".join(lines)


def parameters_table(config: SimulationConfig) -> str:
    """The frozen parameters, as key/value rows."""
    lines = ["| parâmetro | valor |", "|---|---|"]
    for key, value in config.to_dict().items():
        rendered = ", ".join(map(str, value)) if isinstance(value, list) else value
        lines.append(f"| `{key}` | {rendered} |")
    return "\n".join(lines)


def pair_ranking_table(ranking: list[tuple[str, str, float, float]]) -> str:
    """Spearman ranking of the feature pairs on the reference."""
    lines = ["| # | par | Spearman | Pearson |", "|---|---|---|---|"]
    for index, (column_x, column_y, spearman, pearson) in enumerate(ranking, 1):
        pair = f"`{column_x}` / `{column_y}`"
        lines.append(f"| {index} | {pair} | {spearman:+.4f} | {pearson:+.4f} |")
    return "\n".join(lines)


def render(
    config: SimulationConfig,
    main: SimulationResult,
    arms: list[SimulationResult],
    multivariate: list[MultivariateResult],
    ranking: list[tuple[str, str, float, float]],
    extra: dict[str, Any],
) -> str:
    """Assemble the whole report."""
    composition, stress = arms[1], arms[3]
    composition_psi_frame = composition.psi_frame()
    stress_psi_frame = stress.psi_frame()
    last = composition_psi_frame.index[-1]
    composition_psi = float(composition_psi_frame.loc[last].max())
    stress_psi = float(stress_psi_frame.loc[last].max())
    composition_red = int((composition_psi_frame.loc[last] > ALERT_THRESHOLD).sum())
    stress_red = int((stress_psi_frame.loc[last] > ALERT_THRESHOLD).sum())
    composition_gap = composition.months[-1].metrics.calibration_gap
    stress_gap = stress.months[-1].metrics.calibration_gap
    composition_auc = composition.months[-1].metrics.auc_roc
    stress_auc = stress.months[-1].metrics.auc_roc
    two_by_two = "\n".join(
        [
            "| braço | PSI máx (mês 6) | features 🔴 | gap de calibração | AUC |",
            "|---|---|---|---|---|",
            f"| `composition_only` | **{composition_psi:.4f}** | {composition_red} "
            f"| **{composition_gap:+.4f}** | {composition_auc:.4f} |",
            f"| `stress_only` | **{stress_psi:.4f}** | {stress_red} "
            f"| **{stress_gap:+.4f}** | {stress_auc:.4f} |",
        ]
    )
    inflation_shift = abs(
        arms[2].months[-1].metrics.mean_predicted
        - arms[2].months[0].metrics.mean_predicted
    )
    return f"""# Simulação de drift — resumo da execução

Gerado por `make simulate`. Determinístico: a mesma semente produz os mesmos
lotes byte a byte. O cenário, o DAG e o raciocínio estão em
[`docs/simulation.md`](../../docs/simulation.md); este arquivo é só o resultado.

- Fonte: `data/processed/holdout.parquet` ({extra["pool_rows"]:,} linhas). O
  `reference` nunca é tocado.
- Campeão: `{extra["champion_uri"]}`
- Lotes escritos em `data/production/month_XX/` — três arquivos por mês
  (`features`, `predictions`, `labels`), porque em produção eles chegam em
  momentos diferentes.

---

## 1. Parâmetros congelados

{parameters_table(config)}

---

## 2. A rampa de PSI

Dez bins de quantil cortados no `reference`. 🟢 < 0,10 · 🟡 0,10 a 0,25 · 🔴 > 0,25.

{psi_ramp_table(main)}

### Alvos de calibração

{calibration_check(main, config.n_months)}

Os pesos foram ajustados **só** contra estes quatro alvos e então congelados.
Duas variantes mais suaves (`b_util` 0,70 e 0,55) foram testadas e **falham** o
alvo do mês 3 — com elas apenas uma feature cai na faixa 🟡 — sem reduzir de
forma apreciável a taxa de inadimplência final (23,1% e 21,7% contra 24,5%). O
conjunto congelado é o único dos três que atinge os quatro alvos.

---

## 3. Métricas por lote

{metrics_table(main)}

O **gap de calibração** é o sinal de degradação silenciosa, e a tabela mostra
por quê: o AUC cai {extra["auc_drop"]:.1%} em seis meses enquanto a
inadimplência observada mais que **triplica**. Um painel que vigie só
ordenação vê uma degradação moderada; a realidade é que o modelo continua
prevendo {main.months[-1].metrics.mean_predicted:.1%} onde o observado é
{main.months[-1].metrics.observed_default_rate:.1%}.

---

## 4. Ablação — uma intervenção, não uma correlação

Os meses 0 a 6 gerados quatro vezes com a **mesma semente**, variando só quais
mecanismos estão ligados. Como controlamos o processo gerador, ligar e desligar
um mecanismo é intervir, não observar uma associação (ver
`docs/simulation.md` §6).

### AUC-ROC

{ablation_table(arms, "auc_roc", ".4f")}

### Gap de calibração

{ablation_table(arms, "calibration_gap", "+.4f")}

### Inadimplência observada

{ablation_table(arms, "observed_default_rate", ".2%")}

### Previsto médio

{ablation_table(arms, "mean_predicted", ".4f")}

### O que a intervenção mostra — e o que ela refuta

A tabela separa os três tipos de drift de forma limpa:

- **`composition_only` (drift de dados, P(X))** degrada a **ordenação** e
  quase não toca a **calibração**: AUC
  {arms[1].months[0].metrics.auc_roc:.4f} → {arms[1].months[-1].metrics.auc_roc:.4f}
  com gap final de apenas {arms[1].months[-1].metrics.calibration_gap:+.4f}.
  Faz sentido: os rótulos são reais, então clientes mais arriscados de fato
  inadimplem mais e as probabilidades do modelo continuam certas *para eles*.
- **`stress_only` (drift de conceito, P(y|X))** quebra as duas coisas: AUC
  {arms[3].months[0].metrics.auc_roc:.4f} → {arms[3].months[-1].metrics.auc_roc:.4f}
  e gap {arms[3].months[-1].metrics.calibration_gap:+.4f}.
- **`inflation_only` (drift por medição)** é **quase inerte**, e isso
  **contradiz a história desenhada**. O DAG atribuía a degradação silenciosa a
  este mecanismo. A intervenção mostra que a direção está certa e a magnitude
  não: o previsto médio cai de
  {arms[2].months[0].metrics.mean_predicted:.4f} para
  {arms[2].months[-1].metrics.mean_predicted:.4f} — o modelo realmente passa a
  ler risco menor, como o DAG prevê — mas são
  {inflation_shift:.4f}
  de probabilidade, e o AUC não se move.

A razão é **o quanto o campeão usa renda**, medido em estatísticas que a cauda
não contamina (`MonthlyIncome` tem desvio 14.483 e máximo 3.008.750, então
Pearson não serve aqui — ver `docs/findings.md` §8):

{INCOME_EVIDENCE_TABLE}

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

{two_by_two}

**`composition_only`**: o painel de drift grita — PSI de {composition_psi:.2f} na
utilização, {composition_red} features na faixa vermelha — e o modelo continua
**calibrado**, com gap de {composition_gap:+.4f}. Alarme máximo, dano de
calibração nenhum.

**`stress_only`**: **nenhuma feature driftou** — PSI máximo de
{stress_psi:.4f}, mais de dez vezes abaixo do limiar de alerta de 0,10, porque
os rótulos mudaram e as features não. E o gap chega a {stress_gap:+.4f}, doze
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

{multivariate_section(multivariate)}

### Ranking de dependência no `reference`

{pair_ranking_table(ranking)}

### Leitura

Os dois lotes provam coisas diferentes, e é por isso que os dois existem:

O par **primário**, escolhido pela regra de maior dependência entre colunas que
nenhum outro mecanismo toca, produz **resultado nulo**: PSI máximo de
{multivariate[0].max_psi_after:.4f} em toda feature — 🟢 com folga de duas
ordens de grandeza — e ΔAUC de
{multivariate[0].metrics_after.auc_roc - multivariate[0].metrics_before.auc_roc:+.4f},
dentro do ruído de ±0,007 medido no dia 4. Ou seja: **indetectável e
inofensivo**.

O par do **canal de aperto de crédito** (`utilização` / `idade`) é igualmente
indetectável — PSI máximo {multivariate[1].max_psi_after:.4f} — e **não é
inofensivo**: ΔAUC de
{multivariate[1].metrics_after.auc_roc - multivariate[1].metrics_before.auc_roc:+.4f},
fora da banda de ruído. É o argumento mais forte da etapa: existe drift que
degrada o modelo de verdade e para o qual **todo teste univariado é cego por
construção**, porque não há nada de errado com nenhuma marginal.

A moral do par primário não é que o cenário falhou — é que "univariado não vê"
e "não importa" são duas afirmações diferentes, e testar um par só as teria
confundido. O dia 8 precisa dos dois: um mede detectabilidade, o outro mede
dano.
"""


def write(path: Path, content: str) -> Path:
    """Write the report, creating the directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
