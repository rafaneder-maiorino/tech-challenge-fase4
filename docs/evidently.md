# Evidently: versão, API e configuração

Decisões de ferramenta para os relatórios de drift da etapa 2, e as razões.
Curto de propósito: o que interessa aqui é ficar registrado **qual** API foi
escolhida, **por quê**, e o que foi preciso configurar explicitamente para que
os números signifiquem o que parecem significar.

---

## 1. Versão

**`evidently==0.7.23`**, fixada na versão exata em `pyproject.toml` e travada em
`uv.lock`.

Faixa (`>=0.4.40`) não serve aqui. A API do Evidently mudou de forma
incompatível entre as séries 0.4, 0.6 e 0.7, e os relatórios HTML são artefatos
**versionados no repositório**: um arquivo gerado por uma versão diferente da
documentada não é o mesmo artefato, e a faixa deixaria isso acontecer em
qualquer `uv sync` futuro.

## 2. Qual API, e por quê

O material do curso mostra três estilos incompatíveis. Os três existem, e a
forma de decidir não foi ler os PDFs — foi ler o pacote instalado:

| estilo | onde vive na 0.7.23 | status |
|---|---|---|
| `from evidently.report import Report` + `metric_preset` | `evidently.legacy.*` | **legado**, mantido por compatibilidade |
| `from evidently.test_suite import TestSuite` | `evidently.legacy.*` | legado |
| `from evidently import Report, Dataset, DataDefinition` + `evidently.metrics` | topo do pacote | **atual** |

**Escolhido: o estilo atual.** O projeto o expõe no topo do pacote e empurrou o
anterior para um submódulo chamado literalmente `legacy` — não há ambiguidade
sobre qual dos dois a biblioteca considera vigente. Usar o legado significaria
escrever código novo contra uma superfície que o próprio autor marcou como
antiga.

Um detalhe que decidiu o resto do desenho: o estilo atual é o único que aceita
**método de drift por coluna** de forma declarativa, que é exatamente o que a
etapa exige.

```python
from evidently import Report, Dataset, DataDefinition
from evidently.metrics import ValueDrift

Report([ValueDrift(column="MonthlyIncome", method="reference_psi", threshold=0.10)])
```

Uma API só, em todo o projeto. Nada de `evidently.legacy` fora do que é
inevitável — o registro de *stattests* (seção 4) ainda mora lá, e isso está
marcado no código onde acontece.

## 3. Nada de padrão: método por coluna, explícito

Nenhuma métrica é configurada por omissão. Com uma referência de ~103 mil
linhas, a seleção automática do Evidently pode escolher outro teste e deixar de
fora justamente o que a etapa pede.

| o quê | método | limiar |
|---|---|---|
| todas as 11 features | `reference_psi` (seção 4) | 0,10 / 0,25 |
| features contínuas | `ks` (p-valor) | 0,05 |

As **contínuas** são `RevolvingUtilizationOfUnsecuredLines`, `DebtRatio`,
`MonthlyIncome` e `age`. As outras sete são contagens dominadas por empates —
`NumberOfTimes90DaysLate` é zero em 94,56% das linhas — onde um KS compara duas
escadas com um degrau e o resultado não diz nada sobre a forma da distribuição.

### Limiares: 0,10 e 0,25

O material do curso usa **0,20** e **0,25** como limite superior em lugares
diferentes. Adotado **0,25**, que é a convenção clássica de *credit scoring*
(Siddiqi) e a mesma banda que o PSI da etapa 2 já usava — manter dois limiares
diferentes para a mesma estatística no mesmo projeto produziria dois painéis
que discordam sobre a mesma coluna.

### KS: registrado, mas não decide veredito ainda

O `ks` do Evidently devolve o **p-valor** como score de drift, não a
estatística. A estatística é calculada à parte (`scipy.stats.ks_2samp`) para a
tabela de resumo.

Os p-valores são **registrados e não usados para veredito** nesta etapa. Com
~7,4 mil linhas contra uma referência de ~103 mil, o KS tem poder para declarar
significante qualquer diferença, inclusive as que não têm relevância
operacional. Medir esse efeito é trabalho do dia 8 (teste A/A); até lá, o
veredito sai do PSI.

## 4. O `stattest` customizado, e por que foi necessário

O PSI nativo do Evidently **não** tem o bug que a etapa 2 encontrou no nosso —
ele troca para um bin por valor quando a coluna tem 20 ou menos valores
distintos, e nas colunas de contagem os dois concordam em três casas decimais.

O problema está nas contínuas. Para coluna numérica com mais de 20 valores
distintos, `get_binned_data` usa
`np.histogram_bin_edges(combined, bins="sturges")`: bins de **largura igual**
sobre a união de referência e lote, ignorando o parâmetro de número de bins.
Consequências medidas em `docs/findings.md` §10:

- em `MonthlyIncome`, cuja máxima é 3.008.750, a largura de bin resultante é
  **167.152** — praticamente toda a coluna cai num bin só, e o PSI fica
  estruturalmente cego (0,0009 contra 0,0218 nosso);
- `nbinsx` não corrige, porque o caminho numérico **ignora** o parâmetro:
  5, 10, 30, 100 e 300 bins devolvem exatamente o mesmo valor.

A correção é por configuração, como manda a etapa, só que a alavanca não é o
número de bins e sim o próprio teste. O Evidently permite registrar um
*stattest* e referenciá-lo por nome:

```python
StatTest(name="reference_psi", ...)   # bins de quantil cortados no reference
register_stattest(reference_psi_test, _impl)
ValueDrift(column=..., method="reference_psi")
```

`method=` aceita **só string** na API atual — passar um callable é recusado com
erro de validação —, então registrar é o único caminho. O teste registrado
delega para `credit_monitor.simulation.psi`, que é o mesmo código do dia 6, de
modo que o número no HTML do Evidently e o número da nossa tabela são o mesmo
número por construção, e não duas estimativas que por acaso se parecem.

## 5. Os dois tipos de relatório

A separação existe porque os dados chegam em momentos diferentes, e é o 2x2 da
etapa 2 transformado em artefato:

| relatório | entrada | o que enxerga |
|---|---|---|
| **scoring-time** | `features` + `predictions` | drift de feature, drift de score. **Sem rótulo.** |
| **label-time** | `+ labels`, quando "chegam" | drift de alvo, qualidade (AUC, Brier, gap) |

O relatório de scoring-time é o que existiria hoje, em produção, para o lote
deste mês. O de label-time só pode ser produzido meses depois — e é o **único**
capaz de ver o mecanismo de estresse, que muda `P(y|X)` sem mover feature
alguma.

O par gerado sobre o braço `stress_only` no mês 6 é a demonstração: o relatório
de scoring-time fica verde, o de label-time fica vermelho, e os dois descrevem
o mesmo lote.
