# Achados de engenharia

Registro corrido dos problemas que só aparecem quando o código roda contra o dado
real — não os que o enunciado antecipa. Cada achado traz o número que o revelou, o
que ele teria custado se passasse, e como está resolvido no repositório.

O arquivo cresce ao longo do projeto. Achados novos entram no fim, com a etapa e a
data em que foram encontrados.

---

## 1. O nome de um check herdado do Pandera desaparece em silêncio

**Etapa 1 · 2026-09-20 · `src/credit_monitor/contracts/raw.py`**

### O que aconteceu

`ModelInputSchema` herda de `RawCreditSchema`. Cada regra foi declarada com um
`name=` explícito, tirado do registro `Rule`, justamente para que o relatório de
falhas pudesse ser mapeado de volta à severidade. Ao introspeccionar os dois
schemas, os nomes registrados eram:

| schema | nome reportado para a regra de idade |
|---|---|
| `RawCreditSchema` | `age_range` |
| `ModelInputSchema` | `check_age_range` |

O mesmo check, dois nomes.

### A causa

`pandera.api.base.model_components.BaseCheckInfo.to_check` constrói o `Check`
assim:

```python
name = self.check_kwargs.pop("name", None)
if not name:
    name = getattr(self.check_fn, "__name__", ...)
```

O `pop` **muta** o dicionário de kwargs guardado pelo decorador, e uma subclasse
reaproveita o *mesmo* objeto da classe-mãe. O primeiro schema construído consome
o nome; todo schema construído depois cai no `__name__` do método. Qual dos dois
"ganha" depende da ordem de importação — `EXPECTED_COLUMNS` chama
`RawCreditSchema.to_schema()` no import de `raw.py`, então o contrato bruto ficava
com os nomes e o de modelo ficava sem.

### Os números

- **9** das 13 regras são herdadas por `ModelInputSchema` e perderam o nome.
- **3** dessas 9 são de severidade `WARNING`: `no_duplicate_rows`,
  `revolving_utilization_at_most_one` e `positive_rate_band`. Ou seja, **todos os
  alertas do projeto** estavam na faixa afetada.
- `severity_for` falha fechado: nome desconhecido vira `BLOCKER`. Os três alertas
  teriam virado bloqueantes na fronteira do modelo, parando o pipeline por uma
  duplicata — sem nenhuma mensagem de erro, porque tecnicamente nada falhou.

### Como está resolvido

Nenhum `name=` é passado ao `@pa.check`. **O nome do método é o nome da regra**, e
o caminho do `__name__` é determinístico para os dois schemas. O motivo está
escrito por extenso no comentário acima das regras de coluna em `raw.py`, porque a
próxima pessoa a "arrumar" isso passando um `name=` explícito reintroduz o bug.

A amarração é testada nos dois sentidos: `test_severity_mapping_covers_every_
implemented_rule_and_nothing_else` compara o conjunto de nomes que os dois schemas
registram com as chaves de `RULE_SEVERITY` e falha se sobrar ou faltar qualquer
um.

### O que aprendi disso

A busca de severidade da ingestão **se recusa a adivinhar**. Um nome não mapeado
levanta `UnknownRuleError` em vez de cair num padrão, e um *preflight* verifica,
antes de ler qualquer dado, que todo check registrado pelo schema tem severidade.
Com um padrão fechado, esta armadilha teria aparecido como "o pipeline está mais
rígido do que devia" seis meses depois. Falhar fechado é seguro para quem consome
um alerta; é errado para quem é o portão.

---

## 2. `coerce` desligado: o dtype é afirmado, nunca consertado

**Etapa 1 · 2026-09-20 · `src/credit_monitor/contracts/raw.py`**

### O que aconteceu

O caminho natural com Pandera é `coerce=True`: o schema converte o que chega para
o tipo declarado e segue. Nos contadores de inadimplência isso é perigoso, e o
dataset mostra exatamente por quê.

### Os números

- Os três contadores são `uint8`, cujo alcance é **0 a 255**.
- Os códigos administrativos que a inspeção encontrou são **96** e **98** — dentro
  do alcance, então uma conversão não os toca.
- O maior valor legítimo dos três contadores é **13**, **17** e **11**. Não há nada
  entre 17 e 96.
- Um valor **300** vindo de uma origem alterada viraria **44** em silêncio sob
  conversão (`300 mod 256`). Quarenta e quatro é uma contagem de inadimplência
  perfeitamente plausível, dentro da faixa, que nenhuma regra recusaria — e
  fabricada pelo próprio validador.

### Como está resolvido

`coerce = False` no `Config` dos dois schemas. Um dtype diferente do declarado é
reportado como `schema_columns_and_dtypes`, severidade `BLOCKER`, e não absorvido.
Se um nulo aparecer em `age` e alargar a coluna para `float64`, isso é uma mudança
estrutural da origem e é assim que aparece.

O gerador de lote sujo injeta esse defeito de propósito e ele é o mais sutil dos
sete: `age` entregue como `int64` com as **204** idades individualmente legais.
Um schema com `coerce=True` aceitaria o lote sem uma palavra. O nosso bloqueia.

### O que aprendi disso

Conversão silenciosa e validação são objetivos opostos. Um contrato que conserta o
que recebe não é um contrato — é uma etapa de limpeza sem log, e a informação de
que a origem mudou é perdida no exato momento em que era valiosa.

---

## 3. Um check de frame vira uma falha por coluna, não por linha

**Etapa 1 · 2026-09-20 · `src/credit_monitor/pipeline/ingest.py`**

### O que aconteceu

As três regras mais interessantes do contrato (`nested_missingness`,
`no_duplicate_rows`, `positive_rate_band`) são `@pa.dataframe_check`: olham o frame
inteiro porque restringem a *relação* entre colunas ou uma estatística do lote.
Quando uma delas devolve uma série booleana, o Pandera **derrete** o resultado
sobre todas as colunas do frame: uma linha ofensora chega ao `failure_cases` como
uma entrada por célula.

### Os números

No lote de demonstração, com **4** linhas duplicadas injetadas:

| contagem | valor |
|---|---|
| linhas duplicadas injetadas | **4** |
| entradas em `failure_cases` | **44** (4 linhas × 11 colunas) |
| linhas distintas | **4** |

E em `nested_missingness`, com **5** linhas injetadas: **50** entradas — 5 linhas ×
10 colunas, porque a célula `NumberOfDependents` é `NaN` e o Pandera descarta
casos de falha nulos. O total de `failure_cases` do lote é **105** para **20**
defeitos injetados.

No dataset real de 150.000 linhas o efeito é o mesmo em escala: **609** linhas
duplicadas (§7 da inspeção) produzem **6.093** entradas.

### Como está resolvido

`_rejections_from` desduplica por `(regra, linha, coluna)` e, para checks de nível
de frame, descarta a coluna e o valor observado em vez de escolher um dos onze
arbitrariamente — a evidência é a linha inteira, e ela está no parquet da
quarentena. A contagem por regra conta **linhas distintas**, não casos de falha.

É isso que torna o manifesto conferível. Sem a desduplicação o relatório alegaria
44 duplicatas onde o gerador injetou 4, e
`test_report_rule_counts_match_the_injected_manifest` — que compara os dois
dicionários por igualdade — não passaria.

### O que aprendi disso

`failure_cases` é um relatório de células, não de linhas. Qualquer métrica de
qualidade construída contando suas entradas está contando colunas, e o erro é
proporcional à largura da tabela: neste projeto, 11 vezes. Em um relatório para
auditoria, um fator de 11 não é um detalhe de apresentação.

---

<!-- BEGIN: correlation-recheck -->

## 4. As sentinelas fabricavam a correlação de 0,99 entre os contadores

**Etapa 1 · 2026-09-20 · `scripts/recheck_correlation.py`**

### A leitura suspeita

A matriz de §10 da inspeção mostra os três contadores de inadimplência
correlacionados entre si a 0,98-0,99, enquanto cada um correlaciona apenas
0,10-0,13 com o alvo. Ao pé da letra: três medições da mesma variável, e todas
fracas.

A suspeita era de que 0,18% das linhas produzissem o número. Inspeção §6
encontrou 269 registros com os códigos 96/98 nos três contadores **ao mesmo
tempo**, e §4 dá a cada contador desvio padrão ~4,19 contra média ~0,42 — uma
dispersão que só faz sentido se alguns poucos valores perto de 98 estiverem
fazendo o trabalho.

### As duas matrizes

**Dado completo (150,000 linhas)**

| | 30-59d | 90d+ | 60-89d |
|---|---|---|---|
| **30-59d** | 1.0000 | 0.9836 | 0.9870 |
| **90d+** | 0.9836 | 1.0000 | 0.9928 |
| **60-89d** | 0.9870 | 0.9928 | 1.0000 |

**Sem as 269 linhas de sentinela (149,731 linhas)**

| | 30-59d | 90d+ | 60-89d |
|---|---|---|---|
| **30-59d** | 1.0000 | 0.2181 | 0.3059 |
| **90d+** | 0.2181 | 1.0000 | 0.2946 |
| **60-89d** | 0.3059 | 0.2946 | 1.0000 |

### Dispersão, antes e depois

| contador | média | desvio | máx | média (s/ sent.) | desvio (s/ sent.) | máx |
|---|---|---|---|---|---|---|
| `30-59d` | 0.4210 | 4.1928 | 98 | 0.2458 | 0.6978 | 13 |
| `90d+` | 0.2660 | 4.1693 | 98 | 0.0905 | 0.4855 | 17 |
| `60-89d` | 0.2404 | 4.1552 | 98 | 0.0648 | 0.3301 | 11 |

**A hipótese se confirmou**, e com folga. A correlação média entre os contadores cai de
**0.9878** para **0.2729** — uma queda de
**72%** — ao remover **269 linhas**, que são
**0.18%** do dataset. Os 0,18% de linhas produziam
sozinhos quase toda a covariância entre as três colunas.

A aritmética explica: a correlação de Pearson é uma razão de covariâncias, e
covariância é uma soma sobre linhas ponderada pela distância até a média. Com
média ~0,42 e desvio ~4,19, um valor 98 está a cerca de 23 desvios padrão da
média. Duzentas e sessenta e nove linhas nessa posição, movendo-se juntas nas
três colunas ao mesmo tempo, dominam a soma inteira.

### O efeito que ninguém previu

A hipótese dizia respeito à correlação **entre os contadores**. O que apareceu
junto foi mais importante: a correlação de cada contador **com o alvo mais que
dobra** quando as sentinelas saem.

| contador | corr. com alvo (completo) | sem sentinelas | variação |
|---|---|---|---|
| `30-59d` | 0.1256 | 0.2745 | 2.19x |
| `90d+` | 0.1172 | 0.3145 | 2.68x |
| `60-89d` | 0.1023 | 0.2681 | 2.62x |

As mesmas 269 linhas faziam duas coisas ao mesmo tempo: fabricavam uma
colinearidade de 0,99 que não existe, e **escondiam** o poder preditivo real
dos três contadores. Elas carregam o código 98 — o maior valor possível na
coluna — com uma taxa de inadimplência de 54,65%, mas são poucas demais para
puxar a correlação para cima e numerosas demais para não distorcer a escala.
O resultado é uma leitura em que os contadores parecem redundantes e fracos,
quando na verdade são as variáveis mais fortes do conjunto.

### Consequência prática

A leitura ingênua de §10 — "as três colunas são a mesma coisa, e nenhuma
prediz muito" — levaria a descartar duas delas e a desconfiar da terceira. A
leitura correta é o oposto: são três sinais distintos
(correlação entre si de 0.27, não de 0.99) e são os
melhores preditores disponíveis. O pré-processamento remove as sentinelas
antes do treino, então o modelo já vê a versão da direita; esta seção existe
para registrar que a versão da esquerda é uma armadilha de leitura, não um
fato sobre os clientes.

<!-- END: correlation-recheck -->

---

## 5. Tratar o desbalanceamento deixa as probabilidades piores que um chute fixo

**Etapa 1 · 2026-09-20 · `src/credit_monitor/models/train.py`**

### O que aconteceu

O tratamento padrão para uma classe positiva de 6,68% (inspeção §2) é
reponderar: `class_weight="balanced"` na regressão logística,
`scale_pos_weight = 13,974` no XGBoost. Feito isso, o AUC-ROC fica em 0,8584 e
o KS em 0,5677 — números bons. O Brier fica em **0,1356**.

O problema é a referência. Um modelo que ignora as features e responde sempre
a taxa-base 6,68% tem Brier `p(1-p)` = **0,0623**. **O modelo com AUC de 0,86
tem Brier mais que o dobro do pior modelo concebível.**

### Por que

Reponderar é uma mentira deliberada sobre a distribuição a priori: diz ao
estimador que positivos são ~14 vezes mais frequentes do que são. O modelo
aprende a ordenar bem e a reportar probabilidades infladas por
aproximadamente esse fator.

As três métricas reagem de formas diferentes, e é isso que torna o efeito
invisível em quase todo relatório:

| métrica | sensível a transformação monotônica? | efeito da reponderação |
|---|---|---|
| AUC-ROC | não | praticamente nenhum |
| KS | não | praticamente nenhum |
| Brier | **sim** | **duplica** |

AUC e KS só enxergam a *ordem* dos scores, e reponderar não muda a ordem. Um
painel que acompanhe só esses dois nunca vê o problema.

### Os números

Calibração isotônica ajustada numa partição separada (nem treino, nem
validação):

| modelo | AUC s/ calib. | AUC calib. | KS s/ calib. | KS calib. | Brier s/ calib. | Brier calib. |
|---|---|---|---|---|---|---|
| xgboost | 0,8584 | 0,8579 | 0,5677 | 0,5649 | 0,1356 | **0,0496** |
| logistic_regression | 0,8493 | 0,8489 | 0,5466 | 0,5440 | 0,1461 | **0,0499** |

A calibração custa **0,0005 de AUC** e melhora o Brier em **2,7x**.

### Como está resolvido

O conjunto `reference` é particionado em **três** partes, não duas: treino
(60%) ajusta o modelo reponderado, calibração (20%) ajusta o mapa isotônico
sobre ele, e validação (20%) é de onde saem os números reportados. Ajustar o
calibrador nas mesmas linhas em que ele é medido reportaria a memória do
calibrador, não o comportamento dele.

O modelo registrado com o alias `champion` é o calibrado.

### Por que isso importa para as próximas etapas

A etapa 3 acompanha o Brier por lote como sinal de degradação. Esse
acompanhamento pressupõe que o Brier do baseline signifique alguma coisa: com
0,1356 o indicador teria partido de um ponto em que o modelo já era pior que
um chute fixo, e qualquer variação seria ruído sobre um número sem sentido.
Um sinal de degradação calibrado em cima de um modelo descalibrado mede a
calibração, não a degradação.

---

## 6. O teto de utilização: a correlação de -0,00 era a mesma armadilha do achado 4

**Etapa 1 · 2026-09-21 · `src/credit_monitor/data/preprocess.py`**

### O erro

O teto de `RevolvingUtilizationOfUnsecuredLines` foi fixado em **1,0** com a
seguinte justificativa, escrita no próprio parâmetro:

> inspeção §10 põe a correlação linear entre esta coluna e o alvo em **-0,00**,
> então as 3.080 linhas em disputa carregam quase nenhum sinal a perder.

Essa frase foi escrita no mesmo dia, no mesmo repositório, em que o achado 4
documentou que a correlação de Pearson nesse dataset é dominada por outliers e
não deve ser lida ao pé da letra. A coluna tem média 6,05, desvio **249,76** e
máximo **50.708** (§4) — exatamente o regime que o achado 4 descreve. A
justificativa citava como evidência o número que o achado ao lado acabara de
invalidar.

### As três correlações

| subconjunto | n | Pearson | Spearman | AUC univariada |
|---|---|---|---|---|
| (a) dado completo | 150.000 | **-0,0018** | 0,2404 | 0,7778 |
| (b) excluindo > 10 | 149.759 | **+0,2816** | 0,2411 | 0,7786 |
| (c) excluindo > 1 | 146.679 | +0,2494 | 0,2129 | 0,7588 |

Remover **241 linhas** (0,16%) faz o Pearson trocar de sinal e ir de zero a
+0,28. O Spearman, que é de posto e portanto imune à cauda, marcava **0,2404**
desde o começo: a relação real estava visível todo esse tempo em qualquer
estatística robusta. E a coluna sozinha tem **AUC de 0,7778** — não é uma
variável fraca, é uma das mais fortes do conjunto.

### As taxas de inadimplência por faixa

Contra a taxa-base de **6,68%**:

| faixa | linhas | % do dado | inadimplência | vs base |
|---|---|---|---|---|
| ≤ 1 | 146.679 | 97,79% | 5,99% | 0,90x |
| **(1, 10]** | **3.080** | **2,05%** | **39,61%** | **5,93x** |
| > 10 | 241 | 0,16% | 7,05% | 1,06x |

Dentro da faixa em disputa:

| faixa | linhas | inadimplência | vs base |
|---|---|---|---|
| (1, 2] | 2.950 | 40,10% | 6,00x |
| (2, 3] | 79 | 31,65% | 4,73x |
| (3, 5] | 38 | 23,68% | 3,54x |
| (5, 10] | 13 | 23,08% | 3,45x |

A coluna tem **uma** região altamente discriminante e é justamente a que um
teto de 1,0 achata. As linhas acima de 10 inadimplem a 7,05% — indistinguível
da taxa-base, consistente com serem artefato, e é essa a cauda que merece
winsorização.

### O retreino

Mesma semente, mesmo pipeline, só o teto muda. As contagens diferem em 11
linhas (147.349 contra 147.360): achatar em 1,0 colapsa 11 linhas a mais em
duplicatas, o que é um custo adicional pequeno mas real do teto baixo.

| teto | modelo | AUC-ROC | KS | Brier |
|---|---|---|---|---|
| 1,0 | xgboost | 0,8579 | 0,5649 | 0,0496 |
| 10,0 | xgboost | 0,8561 | 0,5636 | 0,0499 |
| 1,0 | logistic_regression | 0,8489 | 0,5440 | 0,0499 |
| 10,0 | logistic_regression | 0,8491 | 0,5451 | 0,0504 |

O teto de 1,0 sai 0,0018 de AUC à frente no XGBoost e 0,0002 **atrás** na
regressão logística. Direções opostas nas duas famílias é o primeiro sinal de
que a diferença é ruído.

### Quanto é ruído

Cinco sementes, mesmo dado, só a partição interna muda:

| semente | AUC teto 1,0 | AUC teto 10,0 | delta |
|---|---|---|---|
| 20260920 | 0,8579 | 0,8561 | -0,0018 |
| 7 | 0,8604 | 0,8542 | -0,0062 |
| 1234 | 0,8560 | 0,8687 | +0,0127 |
| 99 | 0,8659 | 0,8626 | -0,0033 |
| 555 | 0,8670 | 0,8664 | -0,0005 |

- teto 1,0: média **0,8614**, desvio 0,0049, amplitude 0,0110
- teto 10,0: média **0,8616**, desvio 0,0063, amplitude 0,0145
- delta médio: **+0,0002**, com desvio de 0,0073

A diferença de 0,0018 da execução única é **quatro vezes menor que o desvio do
próprio delta**, e na média das cinco sementes o teto de 10,0 sai à frente por
0,0002. **As métricas de modelo não distinguem os dois tetos.**

### Por que o empate nas métricas não absolve o teto de 1,0

Vale entender por que achatar não custa AUC, porque a resposta não é "a cauda
não tem sinal" — ela tem, 39,61% contra 6,68%. É que achatar em 1,0 coloca
todas as 3.321 linhas exatamente em 1,0, e uma árvore ainda separa esse grupo
com um único corte em `utilization >= 1`. A informação "está acima do limite"
sobrevive ao achatamento. O que se perde é a **gradação dentro** da cauda — e
essa gradação é não monotônica (40,10% → 31,65% → 23,68% → 23,08%), ou seja,
quase toda ruído para fins de predição.

Então o empate diz que a árvore recupera o sinal dos dois jeitos. Não diz que
a cauda é descartável.

### A razão que decide: este projeto é um detector de drift

O `reference` tem dois papéis, e eles não têm as mesmas necessidades. Para o
modelo, tanto faz. Para o monitor, não:

Com teto em 1,0, a distribuição de referência **não tem nada acima de 1**. Um
lote futuro em que o grupo acima do limite passe de 1,5 para 8,0 de utilização
média — que é a aparência de um aperto de crédito nesta coluna — chega ao
monitor achatado em 1,0, idêntico à referência. Nenhum teste de KS, nenhuma
distância de Wasserstein, nenhum PSI consegue ver uma diferença que o
pré-processamento apagou antes da comparação. Estaríamos construindo um
detector de drift e deletando a região onde o drift aparece.

O custo dessa capacidade é, pelas cinco sementes, **zero de AUC**.

### Recomendação e decisão

**Teto de 10,0, adotado como padrão antes de congelar o dataset de
referência.** As métricas de modelo são um empate dentro do ruído; o argumento
de observabilidade é assimétrico — 0,0018 de AUC se recupera com ajuste de
hiperparâmetro, uma referência sem cauda é um monitor permanentemente cego
naquela faixa. As 241 linhas acima de 10, que inadimplem na taxa-base, seguem
winsorizadas.

Manter valores acima de 1 deixa `revolving_utilization_at_most_one` falhando, e
essa regra é **alerta** na classificação do próprio contrato. Então
`assert_model_ready` passou a ser consciente de severidade: bloqueante levanta,
alerta é registrado — igual ao que o portão de ingestão já fazia com um lote
entregue. Antes, um alerta parava o pipeline, o que era o passo de
pré-processamento sobrepondo-se à política do contrato.

### O que aprendi disso

Uma justificativa que cita um número precisa citar também como aquele número
foi calculado. "A correlação é -0,00" e "a correlação de Pearson desta coluna é
-0,00 e é dominada por 0,16% das linhas" levam a decisões opostas, e a primeira
frase é a que se escreve quando se está com pressa. O achado 4 já estava no
repositório; o que faltou foi aplicá-lo à coluna seguinte.
