# Análise causal: intervenção, refutação e diagnóstico

**Etapa 4 · `docs/simulation.md` §6 · `reports/simulation/summary.md` §4**

---

## 1. Por que isto é causal, e não correlação bem vestida

Quase toda "análise causal" em produção é uma associação com vocabulário
melhor. Observa-se que a degradação coincidiu com uma mudança na renda, e
conclui-se que a renda a causou. O passo que falta é sempre o mesmo: **não se
pode desligar a renda e olhar de novo.**

Aqui se pode. **O processo gerador dos dados é nosso.** Os meses 0 a 6 são
gerados **quatro vezes com a mesma semente**, variando apenas quais mecanismos
estão ativos:

| braço | composição | inflação | estresse |
|---|---|---|---|
| `all` | ligado | ligado | ligado |
| `composition_only` | ligado | — | — |
| `inflation_only` | — | ligado | — |
| `stress_only` | — | — | ligado |

Isso é uma **intervenção no sentido de Pearl**, não uma observação
condicionada. A distinção é operacional, não filosófica:

| | observação | intervenção |
|---|---|---|
| a quantidade | `P(degradação │ inflação alta)` | `P(degradação │ do(inflação))` |
| o que a produz | filtrar meses em que a inflação foi alta | **gerar** os meses com a inflação ligada e desligada |
| confundidores | abertos — o que mais muda junto? | **fechados por construção** — a semente é a mesma, nada mais mudou |
| contrafactual | inferido | **executado** |

A semente compartilhada é o que fecha as vias. `composition_only` e
`stress_only` no mês 6 partem das **mesmas** 44.208 linhas do holdout, com os
**mesmos** sorteios, e diferem apenas no mecanismo ligado. Não há variável
omitida para discutir: qualquer diferença entre os dois braços é o mecanismo,
porque não sobrou mais nada que pudesse tê-la causado.

Em produção essa tabela não existe. Observa-se o total e discute-se de onde ele
veio. Construí-la agora — quando é barata — é o que permite dizer "a degradação
é **atribuível** a X" em vez de "a degradação **coincide** com X".

## 2. O DAG, com cada aresta rotulada pelo efeito medido

O grafo desenhado antes de qualquer dado (`docs/simulation.md` §1), agora com o
que a intervenção mediu em cada ramo.

```
                                                        efeito medido (mês 3 → mês 6)
                                                        AUC        gap       inadimpl.
Inflação ──┬─► renda nominal ↑ ──────────────┐
           │                                 │
           ├─► DebtRatio ↓ ──────────────────┤──► modelo lê risco MENOR
           │   (mesma dívida / renda maior)  │    do(inflação): 0,8563 →  +0,0014 →  6,60% →
           │                                 │                  0,8658    -0,0049    7,00%
           │                                 │    ┌──────────────────────────────────┐
           │                                 └───►│ QUASE INERTE — o campeão tira    │
           │                                      │ 2,15% do ganho da renda           │
           │                                      └──────────────────────────────────┘
           │
           └─► poder de compra real ↓
                    │
                    └─► utilização do rotativo ↑
                             │
                             └─► atraso 30-59d ↑
                                      │ (defasagem)
                                      └─► 60-89d ↑
                                               │ (defasagem)
                                               └─► 90d+ ↑ ──► inadimplência ↑
                                                    do(estresse): 0,8157 →  -0,0171 →  8,43% →
                                                                  0,7917    -0,0337   10,04%
                                                    ┌──────────────────────────────┐
                                                    │ DOMINA o dano de calibração  │
                                                    └──────────────────────────────┘

Aperto de crédito ──► novos perfis (mais jovens, mais alavancados)
                      do(composição): 0,8455 →  -0,0001 →  12,32% →
                                      0,8130    -0,0028    21,00%
                      ┌────────────────────────────────────────────┐
                      │ DOMINA a queda de ordenação e a mudança de │
                      │ carteira — e não toca a calibração          │
                      └────────────────────────────────────────────┘
```

A tabela por trás do desenho, nos dois meses de corte:

| mecanismo | AUC m3 | AUC m6 | gap m3 | gap m6 | inadimpl. m3 | inadimpl. m6 |
|---|---|---|---|---|---|---|
| nenhum (mês 0) | 0,8601 | 0,8601 | -0,0027 | -0,0027 | 7,08% | 7,08% |
| `do(composição)` | 0,8455 | **0,8130** | -0,0001 | **-0,0028** | 12,32% | **21,00%** |
| `do(inflação)` | 0,8563 | **0,8658** | +0,0014 | **-0,0049** | 6,60% | **7,00%** |
| `do(estresse)` | 0,8157 | **0,7917** | -0,0171 | **-0,0337** | 8,43% | **10,04%** |
| os três juntos | 0,8204 | **0,7779** | -0,0173 | **-0,0396** | 13,95% | **24,46%** |

Três leituras, cada uma de uma linha:

- **Composição move a ordenação e a inadimplência, e não a calibração.** AUC cai
  4,7 pontos e a inadimplência triplica, com gap final de -0,0028 — ruído. Faz
  sentido: os rótulos são reais. Clientes mais arriscados entram na carteira e
  de fato inadimplem mais, e as probabilidades do modelo continuam certas **para
  eles**. A carteira mudou; o significado do score, não.
- **Estresse quebra as duas.** AUC 0,7917 **e** gap -0,0337. É a assinatura do
  drift de conceito: a mesma pessoa, com as mesmas features, passou a
  inadimplir mais.
- **Inflação é quase inerte, e o AUC até sobe.** É o ponto do §3.

## 3. A refutação: o DAG acertou a direção e errou a magnitude

**A história desenhada atribuía a degradação silenciosa à inflação nominal.**
O raciocínio estava escrito antes de qualquer dado: renda nominal sobe,
`DebtRatio` cai, o modelo lê risco menor do que o real, e a degradação passa
despercebida porque nenhum rótulo mudou.

A intervenção mediu, e a história não sobreviveu.

| o que o DAG previu | o que `do(inflação)` produziu |
|---|---|
| o modelo lê risco **menor** | ✅ **confirmado** — previsto médio 0,0681 → 0,0652 |
| e isso causa a degradação silenciosa | ❌ **refutado** — são **29 pontos-base**, e o AUC não cai (0,8601 → 0,8658) |
| a degradação silenciosa vem da medição | ❌ vem do **estresse**: gap **-0,0337** contra **-0,0049** |

**A comparação decisiva é -0,0337 contra -0,0049: o estresse causa quase sete
vezes mais dano de calibração que a inflação.** A direção do DAG estava certa —
o modelo de fato passa a ler risco menor sob inflação. A magnitude estava errada
por uma ordem de grandeza.

### Por quê: o campeão quase não usa renda

A razão não é que a inflação seja um mecanismo fraco. É que **este modelo mal
olha para a coluna que ela move**, medido em três estatísticas que a cauda não
contamina (`MonthlyIncome` tem desvio 14.483 e máximo 3.008.750, então Pearson
não serve aqui — `findings.md` §8):

| evidência | `MonthlyIncome` | topo da lista | posição |
|---|---|---|---|
| ganho do XGBoost | **2,15%** | 90d+, 28,47% | 9 de 11 |
| importância por permutação (queda de AUC) | **+0,0029** | utilização, +0,0757 | 7 de 11 |
| AUC univariada | 0,5746 | utilização, 0,7770 | 6 de 11 |

A renda **tem** sinal univariado (0,5746, meio da tabela). O modelo simplesmente
não o usa: as quatro primeiras features — três contadores de atraso e a
utilização — concentram **82,07%** do ganho. Mover 10% uma coluna que responde
por 2% do modelo desloca a previsão média em 29 pontos-base, que é exatamente o
que a intervenção mediu.

### O que a refutação **não** diz

Inflação nominal continua sendo um mecanismo real de drift por medição. A
intervenção mostra que **neste modelo** ela é pequena, e mostra por quê. Num
*scorecard* que usasse renda de forma central — o que é comum — a mesma
intervenção daria outro resultado. O efeito é do par *(mecanismo, modelo)*,
nunca do mecanismo sozinho.

E o registro que importa para o método: **o DAG foi escrito antes e mantido
depois, com a refutação ao lado.** Reescrevê-lo para concordar com a medição
transformaria uma previsão falsificada em sabedoria retrospectiva, e é a única
coisa que este exercício não pode produzir.

## 4. A consequência diagnóstica: assinatura → mecanismo

Este é o pagamento prático. A intervenção não serve só para explicar o passado:
ela dá a **assinatura** de cada mecanismo, e assinaturas se leem ao contrário.
Dada uma degradação observada, o padrão de sinais identifica a causa.

### A tabela de decisão

Para quem está de plantão. Lê-se da esquerda para a direita; a primeira linha
que casar é a resposta.

| # | drift de feature | gap de calibração | contrato | **mecanismo provável** | evidência | o que fazer | runbook |
|---|---|---|---|---|---|---|---|
| 1 | **sim** (PSI > 0,25) | **intacto** (> -0,0056) | ok | **composição da carteira** — mudou *quem* entra, não o que o risco significa | `do(composição)`: PSI máx **0,9327**, gap **-0,0028** | **Não retreinar por isto.** A carteira mudou e o modelo continua certo sobre ela. Verificar se a mudança foi intencional (campanha, canal novo, política de crédito) | [`FeatureDrift`](runbooks/FeatureDrift.md) · [`PredictionDrift`](runbooks/PredictionDrift.md) |
| 2 | **não** (PSI < 0,10) | **rompido** (≤ -0,0056) | ok | **mudança de conceito** — a mesma pessoa passou a inadimplir mais | `do(estresse)`: PSI máx **0,0082**, gap **-0,0337** | **Retreinar é a única saída.** Nenhum ajuste de limiar recupera um score que parou de significar o que diz | [`CalibrationGapBreach`](runbooks/CalibrationGapBreach.md) · [`AUCDrop`](runbooks/AUCDrop.md) |
| 3 | **sim** | **rompido** | ok | **os dois juntos** — é o cenário `all` | gap **-0,0396**, AUC **0,7779** | Tratar como o caso 2 (o conceito domina) e investigar a composição em paralelo | [`CalibrationGapBreach`](runbooks/CalibrationGapBreach.md) |
| 4 | **não** | **intacto** | **violado** | **mudança na origem** — esquema, dtype, sentinela, unidade | as **269** linhas de sentinela 96/98 (§5) | **Não é degradação do modelo.** É o fornecedor do dado. O lote está em quarentena e não foi pontuado | [`ContractBlocked`](runbooks/ContractBlocked.md) |
| 5 | **não** | **intacto** | ok, lote pequeno | **nada** — o monitor se absteve | `min_batch_size = 1000`, medido no A/A | Não é verde: é ausência de veredito. Agregar lotes antes de concluir | [`InsufficientSample`](runbooks/InsufficientSample.md) |
| 6 | qualquer | **desconhecido** | ok | **indeterminável ainda** — o rótulo não chegou | `label_lag_months = 2` | Nenhuma linha acima pode ser decidida. Registrar e esperar | [`LabelsPending`](runbooks/LabelsPending.md) |

### Por que a tabela funciona, e por que ela é incompleta

Funciona porque as assinaturas são **separáveis**, e a separação foi medida e
não presumida: `composition_only` chega a PSI 0,9327 com gap -0,0028;
`stress_only` fica em PSI 0,0082 com gap -0,0337. Os dois cantos opostos do 2x2
da etapa 2 são exatamente as linhas 1 e 2.

É incompleta em dois pontos que precisam ficar visíveis:

1. **A linha 6 é a mais frequente na prática.** O gap de calibração exige o
   desfecho, e o desfecho chega **dois meses depois**. Por dois meses, toda
   degradação observável cai na linha 6 e nenhuma das outras pode ser decidida.
   É o achado §13, e é por isso que o lead time é **-1** no `full` e **-2** no
   `stress_only`: **nenhum alerta chega antes do dano.**
2. **A linha 4 é a única que o sistema pega na hora**, porque o contrato não
   precisa de rótulo. É também a única em que a ação correta não envolve o
   modelo.

### A nota do dia 4 que pertence aqui

As **269 linhas** com os códigos-sentinela 96/98 nos contadores de atraso são
removidas pelo pré-processamento e **não estão no `reference`**. Elas
inadimplem a **54,65%**, contra 6,68% da base.

A consequência diagnóstica é a linha 4 da tabela. Se um lote de produção chegar
carregando sentinelas — porque o fornecedor voltou a emiti-las, ou porque um
*join* trouxe um sistema legado de volta —, **a referência não tem nada parecido
com aquilo**. O contrato as bloqueia antes do modelo (`delinquency_sentinels`,
severidade BLOCKER), o que é o comportamento certo. Mas vale ser explícito sobre
o que esse acerto significa: o sistema está recusando decidir sobre a população
de **maior risco medido do dataset**. É contenção de escopo, não solução, e está
registrado como limitação no Model Card.

## 5. O limite do que esta análise pode afirmar

Esta é a seção que o documento existe para não deixar de fora.

**Os mecanismos foram escolhidos por nós.** Composição, inflação e estresse não
foram descobertos nos dados — foram escritos em `src/credit_monitor/simulation/`,
com magnitudes que também escolhemos. A intervenção identifica efeitos **dentro
do mundo simulado**, e só.

O que isso permite e o que não permite:

| a análise **estabelece** | a análise **não estabelece** |
|---|---|
| que, dado este modelo, mudança de conceito danifica calibração muito mais que inflação nominal | que, numa economia real, mudança de conceito domina inflação |
| que as assinaturas dos três mecanismos são separáveis pelos sinais que o sistema já coleta | que os mecanismos reais produzem assinaturas igualmente limpas |
| que o campeão é quase insensível a renda, porque dela tira 2,15% do ganho | que um modelo de crédito qualquer seja insensível a renda |
| que a tabela de decisão do §4 é válida **para estes mecanismos** | que ela cubra as causas de degradação que não simulamos |

A afirmação honesta é esta: **a estrutura causal é real, as magnitudes são
nossas.** As direções — drift de dados degrada ordenação, drift de conceito
degrada calibração — decorrem do que cada mecanismo é, e transferem. Os números
— 0,0337 contra 0,0049 — decorrem de quanto de cada mecanismo escolhemos aplicar
e de quanto este modelo usa cada coluna, e **não** transferem.

O que transfere, e é o entregável de verdade, é o **método**: um sistema de
monitoramento que consegue distinguir mecanismos pela assinatura vale mais que
um que só sabe dizer "piorou". Se as magnitudes reais forem outras, a tabela do
§4 se recalibra com uma execução da ablação sobre os mecanismos certos. A
estrutura permanece.

E uma restrição que o próprio dataset impõe: a análise de viés
([`docs/vies.md`](vies.md) §8) mostrou que o mecanismo que move a demografia
(composição) **não** é o que causa o dano de calibração por faixa (estresse).
Essa conclusão vale sob a mesma fronteira — dentro do mundo simulado —, e é um
bom exemplo do porquê a fronteira importa: ela é exatamente o tipo de resultado
que seria tentador generalizar.
