"""Run the A/A test and the significance-vs-magnitude sweep, write the report.

Run as ``uv run python scripts/aa_test.py`` (or ``make aa-test``). Reads the
MMD cache written by ``make mmd`` if it is there, so one summary carries every
stage-2 statistical result.
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from credit_monitor.constants import PROCESSED_DATA_DIR, REPORTS_DIR
from credit_monitor.drift_tests.aa import REPEATS, AAResult, run_aa
from credit_monitor.drift_tests.sensitivity import run_sweep
from credit_monitor.logging_config import configure_logging
from credit_monitor.models.score import load_champion
from credit_monitor.reporting import drift_tests_report as report
from credit_monitor.reporting.drift_tests_chart import render_false_alarm_chart

log = logging.getLogger(__name__)

CACHE_DIR = Path("data/drift_tests")
OUT_DIR = REPORTS_DIR / "drift_tests"
SEED = 20260922


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/aa_test.py")
    parser.add_argument("--data-dir", type=Path, default=PROCESSED_DATA_DIR)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--reuse", action="store_true", help="usa o cache do A/A")
    return parser


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    reference = pd.read_parquet(args.data_dir / "reference.parquet")
    holdout = pd.read_parquet(args.data_dir / "holdout.parquet")

    raw_path = args.cache_dir / "aa_raw.parquet"
    if args.reuse and raw_path.exists():
        raw = pd.read_parquet(raw_path)
    else:
        raw = run_aa(reference, holdout, args.seed).frame
        raw.to_parquet(raw_path, index=False)
    result = AAResult(frame=raw, reference_rows=len(reference), repeats=REPEATS)

    sweep_path = args.cache_dir / "sensitivity.parquet"
    if args.reuse and sweep_path.exists():
        sweep = pd.read_parquet(sweep_path)
    else:
        sweep = run_sweep(reference, holdout, load_champion(), args.seed)
        sweep.to_parquet(sweep_path, index=False)

    chart = render_false_alarm_chart(
        result.family_wise_rates(), args.out_dir / "false_alarm_rate.png"
    )

    mmd_section = "_Rode `make mmd` para preencher esta seção._"
    if (args.cache_dir / "mmd_batches.parquet").exists():
        table, self_aa, localization = report.mmd_tables(args.cache_dir)
        mmd_section = f"""{table}

O lote de controle **não é detectado** (p = 0,65), que é a condição mínima para
o teste valer alguma coisa. Os dois lotes só-multivariados **são** detectados no
piso do p-valor permutacional, e o PSI univariado deles é 0,0041 em toda
feature — verde com duas ordens de grandeza de folga.

### O A/A do próprio MMD

{self_aa}

### Localização: MMD diz *que* mudou, não *onde*

{localization}

Nos dois lotes o par invertido sai em primeiro. No primário, os pares seguintes
também envolvem `NumberRealEstateLoansOrLines`: permutar uma coluna muda a
dependência dela com **todas** as outras, não só com a parceira, então a
localização aponta para a coluna tanto quanto para o par."""

    family_wise = report.family_wise_table(result)
    content = f"""# Testes estatísticos de drift — etapa 2

Gerado por `make aa-test` e `make mmd`. Determinístico a partir da semente
{args.seed}. Referência: {len(reference):,} linhas; holdout: {len(holdout):,}.

---

## 1. Teste A/A: quantos alarmes numa população sem drift

{REPEATS} amostras do holdout por tamanho, comparadas contra a referência
completa. O holdout foi lacrado na etapa 1 exatamente para isto, e os dois
conjuntos são disjuntos por construção. **Todo alarme aqui é falso.**

![Taxa de falso alarme por regra](false_alarm_rate.png)

### Taxa por lote (qualquer feature dispara)

{family_wise}

### A afirmação original estava errada

O plano vinha dizendo que "KS gera falso alarme com amostra grande". Sob o nulo
verdadeiro os p-valores do KS são uniformes, então a taxa **por feature** fica
em alfa para qualquer n. É o que se mede:

{report.ks_by_type_table(result)}

Nas contínuas a taxa orbita alfa = 0,05 em todos os tamanhos. Nas contagens ela
fica **cinco a dez vezes abaixo**, porque os empates tornam o KS conservador —
nos três contadores de atraso ela é exatamente **0,000 em todos os n**.

O problema do KS não é a taxa por feature. São **múltiplas comparações**: onze
features a 5% dão de 12% a 25% de lotes limpos acionando o painel, e isso quase
não depende de n. Bonferroni e Benjamini-Hochberg derrubam para 0,5%-3,0%, e
**coincidem em todos os tamanhos** — com no máximo um p-valor pequeno sob o
nulo, o corte do BH no primeiro posto é o próprio Bonferroni.

### O PSI falha no regime oposto, e de forma previsível

{report.psi_bias_table(result, reference)}

A razão entre medido e previsto fica entre 0,94 e 1,21: o viés de pequena
amostra do PSI **não é mistério**, é `(bins-1)(1/n + 1/m)`. Quem paga são as
colunas com mais bins:

{report.small_n_offenders_table(result, reference)}

Isso é o custo da correção do dia 6. A regra de um bin por valor consertou a
cegueira do PSI nas colunas 94% zeradas, e em troca deu a elas 17 e 26 bins —
mais bins, mais viés a n pequeno. `NumberRealEstateLoansOrLines` tem viés
previsto de 0,1002 a n = 250, que é o limiar de alerta.

### Recomendação operacional para n ≈ 7.000

| regra | falso alarme medido | veredito |
|---|---|---|
| KS sem correção | **21,5%** | inutilizável como portão de lote |
| KS + Bonferroni | 1,5% | usável, mas responde à pergunta errada (ver §2) |
| PSI > 0,10 | **0,0%** | usável como alerta |
| PSI > 0,25 | **0,0%** | usável como bloqueio |

**Adotar PSI com 0,10/0,25 como regra de veredito, e manter o KS como
diagnóstico com Bonferroni.** No nosso tamanho de lote o PSI não deu **nenhum**
falso alarme em 200 sorteios (o que limita a taxa real a menos de 1,5% com 95%
de confiança), e ele mede magnitude, que é o que falta ao KS. O KS sem correção
pintaria o painel de vermelho em mais de um de cada cinco lotes perfeitamente
normais.

A recomendação **depende do tamanho do lote**: abaixo de n ≈ 500 o PSI > 0,10
inverte e passa a ser o pior dos dois (28,5% a n = 250). Um limiar calibrado num
tamanho de lote está errado em outro.

### Regra operacional: tamanho mínimo de lote

> **Abaixo de n = 1.000 os limiares de PSI não valem. O veredito tem de ser
> `INSUFFICIENT_SAMPLE` — nunca verde, nunca vermelho.**

O viés de pequena amostra do PSI atinge o **limiar de alerta** a n = 250 na
coluna de 26 bins: viés previsto de 0,1002 contra um limiar de 0,10. Nesse
tamanho, 28,5% dos lotes sem drift algum disparavam.

Um lote pequeno não torna o drift menos provável — torna a medição incapaz de
separar drift do próprio viés. Pintar verde afirmaria estabilidade que não foi
medida; pintar vermelho afirmaria drift que pode ser só o estimador. A única
resposta honesta é recusar o veredito.

O piso é 1.000 e não 500: a 500 o falso alarme já caiu para 1,0%, mas a margem
é estreita e o viés médio ainda é 0,0212. **1.000 é o primeiro tamanho com 0,0%
medido.** Registrado em `configs/monitoring.yaml` como `min_batch_size`;
`credit_monitor.reporting.drift.verdict` já o aplica quando recebe o tamanho do
lote, e a etapa 3 o transforma em portão.

---

## 2. Significância não é magnitude

Varredura do mecanismo de inflação sozinho — o que a ablação mostrou ser quase
inerte.

### DebtRatio

{report.sensitivity_table(sweep, "DebtRatio")}

### MonthlyIncome

{report.sensitivity_table(sweep, "MonthlyIncome")}

A n = 44.000 com π = 0,01 — **um por cento** de inflação — o KS na renda dá
p = 6e-11 enquanto o PSI marca 0,0015, a previsão média se move 3,5 pontos-base
e o AUC muda na quarta casa. O mesmo deslocamento, na mesma direção, é
**invisível** ao KS a n = 1.000 (p = 0,20).

O veredito do KS é decidido pelo tamanho do lote, não pelo tamanho do efeito. É
um alarme verdadeiro — a distribuição mudou mesmo — e operacionalmente inútil.

---

## 3. MMD: o drift que nenhuma marginal revela

Features transformadas em **escores normais de posto** ajustados na referência,
kernel RBF com largura pela heurística da mediana, teste de permutação com
{1000} permutações e n = 2.000 por lado. Custo: ~1,1 s para os quatro testes,
porque as permutações saem de um produto de matrizes sobre o kernel
pré-computado em vez de recomputá-lo mil vezes.

Escores de posto e não valores brutos porque o kernel é função de distância:
uma coluna com desvio 249,76 dominaria toda distância par a par e o teste
mediria só ela. É a lição dos achados §4, §6 e §8 aplicada a kernels.

{mmd_section}

---

## 4. O que fica para a etapa 3

- Veredito por **PSI** (0,10 alerta / 0,25 bloqueio) no tamanho de lote atual.
- **KS só com correção** e só como diagnóstico, nunca como portão.
- **MMD como segunda linha**, porque é o único que vê dependência — e com
  localização por par, senão ele diz que algo mudou sem dizer o quê.
- Nenhum limiar univariado é transportável entre tamanhos de lote.
"""
    (args.out_dir / "summary.md").write_text(content, encoding="utf-8")

    print()
    print("TAXA DE FALSO ALARME POR LOTE")
    print("=============================")
    print(family_wise)
    print()
    print("KS POR TIPO DE FEATURE")
    print("======================")
    print(report.ks_by_type_table(result))
    print()
    print(f"Grafico : {chart}")
    print(f"Relatorio: {args.out_dir / 'summary.md'}")
    print()


if __name__ == "__main__":
    main()
