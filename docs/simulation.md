# Simulação de drift em produção

Seis meses de um choque inflacionário com aperto de crédito, aplicados ao
conjunto `holdout` da etapa 1. O objetivo não é produzir um dataset difícil: é
produzir um dataset em que **sabemos qual é a resposta**, para depois verificar
se o monitor a encontra.

---

## 1. A história causal

A inflação sobe. Três coisas acontecem ao mesmo tempo, e só uma delas é visível
para o modelo:

1. **A renda nominal sobe.** O salário em reais aumenta. O modelo lê renda mais
   alta e conclui: cliente mais seguro.
2. **O `DebtRatio` cai.** A dívida contratada é a mesma, o denominador subiu. O
   modelo lê menos alavancagem e conclui, de novo: cliente mais seguro.
3. **O poder de compra real cai.** O salário sobe menos que os preços. A família
   encosta no crédito rotativo para fechar o mês.

O terceiro efeito é o real e é o que o modelo **não** mede diretamente. A
utilização do rotativo sobe, os atrasos de 30-59 dias aparecem primeiro, e com
defasagem cascateiam para 60-89 e depois para 90+. Em paralelo, o aperto de
crédito muda quem entra na carteira: perfis mais jovens e mais alavancados, que
antes seriam aprovados em outras linhas.

**É por isso que a degradação é silenciosa.** As duas variáveis que o modelo
mais usa para dizer "seguro" — renda e alavancagem — andam na direção errada
exatamente quando o cliente fica mais arriscado. O modelo não fica confuso: ele
fica **confiante e errado**, e continua prevendo ~6% de inadimplência enquanto a
realidade sobe. O sinal disso é o **gap de calibração** (previsto médio menos
observado), não o AUC — a ordenação entre clientes se degrada muito mais devagar
que o nível absoluto.

### O DAG

```
Inflação ──┬─► renda nominal ↑ ──────────────► modelo lê risco MENOR
           │
           ├─► DebtRatio ↓ ──────────────────► modelo lê alavancagem MENOR
           │   (mesma dívida / renda maior)
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

Aperto de crédito ──► novos perfis (mais jovens, mais alavancados)
```

Os dois ramos de cima são **medição**: a pessoa não mudou, o que mudou foi o
número que chega ao modelo. O ramo de baixo é **realidade**: a pessoa mudou.
Essa distinção é a razão de existirem três mecanismos separados e uma ablação.

---

## 2. Fonte dos dados

**Só o `holdout`.** As 44.208 linhas reservadas na etapa 1. O `reference` nunca
é tocado: ele é a linha de base contra a qual todo drift é medido, e uma
referência que participasse da simulação mediria a si mesma.

Tamanho do lote: **7.368 linhas por mês** (44.208 / 6, arredondado).

Intensidade: **s = mês / 6**, para os meses 1 a 6. O **mês 0** é um lote de
controle com **s = 0** — nenhum mecanismo é aplicado, e é ele que estabelece
qual é o ruído de amostragem antes de qualquer sinal.

---

## 3. Os três mecanismos

Cada um corresponde a um tipo diferente de drift, e é por isso que estão
separados: o monitor da etapa 2 tem de acertar não só *que* houve drift, mas
*qual*.

### Mecanismo 1 — composição da carteira (drift de dados puro, P(X))

Amostragem ponderada de clientes **reais** do holdout. Nenhum cliente
impossível é criado: o rótulo é real, as features são reais e a distribuição
conjunta entre as colunas continua sendo a que existe no dado.

```
w_i = exp( s · [ b_util·r(util) + b_age·(−r(age)) + b_d30·r(atraso_30_59)
               + b_d60·lag(mês,2)·r(atraso_60_89)
               + b_d90·lag(mês,3)·r(atraso_90) ] )
```

**`r()` é o escore normal baseado em posto, calculado no `reference`** — não um
z-score. Isso não é preciosismo estatístico, é a aplicação direta dos achados 4
e 6: nessas colunas exatamente, momentos brutos são dominados por uma fração de
um por cento das linhas. A utilização tem desvio 249,76 e máximo 50.708 (§4), e
os contadores de atraso tinham desvio ~4,19 contra média ~0,42 antes de as
sentinelas saírem. Um z-score aqui daria a algumas dezenas de linhas peso de
amostragem astronômico, e a "deriva de composição" resultante seria a mesma
armadilha dos dois achados, reintroduzida pela porta dos fundos. O escore de
posto usa **postos médios** para os empates, o que importa porque os contadores
são zero na grande maioria das linhas.

**`lag(mês, k)`** vale 0 antes do mês `k` e depois sobe em rampa. É o que faz as
faixas de atraso mais graves começarem a se mover **depois** das mais leves,
reproduzindo a cascata do DAG em vez de mover as três juntas.

**Amostragem sem reposição dentro do lote.** Com reposição, o mesmo cliente
apareceria duas vezes e o lote acionaria o alerta de duplicatas do contrato —
um artefato do simulador sendo lido como drift. A implementação usa o truque de
Gumbel top-k, que é amostragem ponderada sem reposição exata (Plackett-Luce) e
determinística dada a semente.

### Mecanismo 2 — inflação nominal (drift de conceito por medição)

Só nas linhas com renda presente (`income_missing == 0`):

```
MonthlyIncome ·= (1 + π_total · s)
DebtRatio     /= (1 + π_total · s)       # mesma dívida nominal, renda maior
```

**Os rótulos não são tocados.** A pessoa e o risco real dela são os mesmos; o
que mudou foi o que o modelo mede. É a definição de drift de conceito por
medição, e é o mecanismo que produz a degradação silenciosa.

As linhas **sem** renda ficam de fora, e não por conveniência: o `DebtRatio`
delas está em outra unidade — mediana 1.159 contra 0,296 (inspeção §9) — e no
pré-processamento foi neutralizado para a mediana do grupo com renda. Aplicar a
fórmula da razão a elas seria dividir por uma renda que não existe.

`π_total = 0,10` no parâmetro congelado.

### Mecanismo 3 — estresse macroeconômico (drift de conceito verdadeiro, P(y|X))

Rótulos viram **0 → 1 apenas, nunca 1 → 0**. A recessão faz gente boa
inadimplir; não faz inadimplente virar bom pagador retroativamente.

A probabilidade de virada é proporcional a `s` vezes o **posto** da utilização,
com a escala resolvida analiticamente para que em `s = 1` a taxa de
inadimplência suba `extra_default_rate_at_full` (0,03, ou +3 pontos
percentuais) **em cima** do que o mecanismo 1 já produziu:

```
c = n · extra / Σ_{y=0} u_i        p_i = min(1, c · s · u_i)
```

Assim a inadimplência extra esperada é exatamente `s · extra` e cresce linear
com o mês, sem nenhuma constante ajustada à mão. A contagem realizada de
viradas é registrada mês a mês.

---

## 4. O cenário só-multivariado

Um lote separado, para o teste MMD do dia 8, com uma propriedade deliberada:
**toda distribuição marginal é idêntica à do mês 0 e a dependência entre duas
colunas está invertida**. Se o modelo se degrada nesse lote, e ele se degrada,
então todo teste univariado do monitor está cego para uma classe inteira de
drift.

A dependência é invertida por **permutação**: os valores de uma coluna são
reatribuídos entre as linhas por casamento de postos com um termo de ruído,
calibrado para um Spearman alvo de aproximadamente menos o valor de referência.
Como é permutação, o multiset de cada coluna é idêntico por construção — e isso
é **afirmado**, não presumido.

### A escolha do par

Spearman medido no `reference`, os cinco primeiros:

| par | Spearman | Pearson |
|---|---|---|
| `DebtRatio` / `NumberRealEstateLoansOrLines` | **0,5369** | 0,3795 |
| `NumberOfOpenCreditLinesAndLoans` / `NumberRealEstateLoansOrLines` | **0,4640** | 0,4272 |
| `DebtRatio` / `NumberOfOpenCreditLinesAndLoans` | 0,3523 | 0,2517 |
| `MonthlyIncome` / `NumberRealEstateLoansOrLines` | 0,3514 | 0,1039 |
| `NumberOfTimes90DaysLate` / `NumberOfTime60-89DaysPastDueNotWorse` | 0,2961 | 0,2909 |

O par mais forte é `DebtRatio` / `NumberRealEstateLoansOrLines`, e a dependência
é real e não artefato da nossa imputação: restrita às linhas com renda presente
ela sobe para **0,6019**, ou seja, o bloco de empates criado pela neutralização
do `DebtRatio` estava **diluindo** a medida, não inflando.

**O par escolhido é o segundo**, `NumberOfOpenCreditLinesAndLoans` /
`NumberRealEstateLoansOrLines` (Spearman 0,4640, Pearson 0,4272). A razão é
atribuição, não força: o `DebtRatio` é dividido por `(1 + π·s)` no mecanismo 2,
então um lote só-multivariado construído sobre ele não poderia ser atribuído
com clareza — a degradação seria de dependência ou de medição, e não saberíamos
qual. O par escolhido é o mais forte **entre as colunas que nenhum outro
mecanismo toca**, e é economicamente interpretável: as duas medem amplitude de
carteira, e o aperto de crédito é exatamente o que descasa uma da outra.

---

## 5. O resultado que a ablação existe para produzir

Antes da mecânica: este é o resultado que o desenho todo serve para tornar
mensurável, e ele é contraintuitivo o bastante para valer o cenário inteiro.

| braço | PSI máx (mês 6) | features 🔴 | gap de calibração | AUC |
|---|---|---|---|---|
| `composition_only` | **0,9327** | 5 de 11 | **-0,0028** | 0,8130 |
| `stress_only` | **0,0082** | **0 de 11** | **-0,0337** | 0,7917 |

A composição da carteira muda e o painel de drift grita, enquanto o modelo
segue **calibrado** — os rótulos são reais, clientes mais arriscados de fato
inadimplem mais, e as probabilidades continuam certas para eles. O estresse
macroeconômico não move **nenhuma** feature — PSI máximo na ordem do ruído de
amostragem — e destrói a calibração.

> **Drift não é degradação, e degradação não exige drift.**

A consequência prática: monitorar drift de features não pode, sozinho, pegar o
mecanismo que causa a maior parte do dano. `stress_only` muda `P(y|X)` sem tocar
em `P(X)`, e nenhuma distância entre distribuições de entrada alcança isso.
**Monitoramento baseado em rótulo — rótulos com atraso, gap de calibração,
Brier por lote — é obrigatório, não complementar.** É também o mais lento, o que
é precisamente o motivo de os lotes saírem em três arquivos separados (§7).

---

## 6. A ablação é uma intervenção, não uma correlação

Os meses 0 a 6 são gerados quatro vezes, com a mesma semente: todos os
mecanismos ligados, só composição, só inflação, só estresse.

Isso merece ser dito com precisão, porque é a diferença entre esta análise e
quase tudo que se chama de "análise causal" em produção: **nós controlamos o
processo gerador de dados**. Ligar e desligar um mecanismo é intervir em `do(X)`
no sentido de Pearl, não observar uma associação. Quando a tabela de ablação
mostra que o gap de calibração vem do mecanismo 2 e a queda de AUC vem do
mecanismo 3, isso não é uma hipótese sobre qual efeito domina — é a leitura
direta de uma intervenção, com as outras vias fechadas por construção.

Em produção essa tabela não existe: só se observa o total e se discute qual
parcela vem de quê. Construí-la agora, quando é barata, é o que vai permitir ao
dia 12 dizer "a degradação é atribuível a X" em vez de "a degradação coincide
com X".

---

## 7. Atraso de rótulo

Cada lote é escrito em **três arquivos separados**, e a separação é o ponto:

| arquivo | conteúdo | quando existe na realidade |
|---|---|---|
| `features.parquet` | o que o monitoramento vê | no momento da pontuação |
| `predictions.parquet` | probabilidades do campeão | no momento da pontuação |
| `labels.parquet` | o desfecho | meses depois |

A função de pontuação recebe **somente features** e recusa um frame que
contenha a coluna alvo. Isso não é defensivo por gosto: um monitor que possa
tocar no rótulo no momento da pontuação é um monitor que vai, em alguma versão
futura, medir a própria degradação usando informação que ainda não teria. A
única forma de garantir que isso não aconteça é a assinatura não permitir.

É também o que torna o gap de calibração o sinal interessante: ele é a única
métrica desta lista que precisa do rótulo, portanto a única que só se pode
calcular com atraso. As métricas que se pode calcular hoje (PSI, distâncias de
distribuição) são justamente as que não olham para `y`.
