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

**Etapa 1 · 2026-09-21 · `scripts/recheck_correlation.py`**

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

---

## 7. Dois bugs que pareciam resultados

**Etapa 2 · 2026-09-21 · `src/credit_monitor/models/score.py`, `simulation/psi.py`**

Os dois apareceram na primeira execução completa do simulador, os dois
produziram números plausíveis, e é essa a parte que interessa: numa simulação
de drift, **degradação é o resultado esperado**, então qualquer bug que degrade
alguma coisa se disfarça de sucesso.

### Bug A — `pyfunc.predict` devolve classe, não probabilidade

O campeão foi carregado com `mlflow.pyfunc.load_model` e pontuado com
`model.predict(X)`. Um wrapper pyfunc expõe só `predict`, que num
classificador devolve a **classe prevista**. Então:

| métrica | com o bug | correto |
|---|---|---|
| AUC no mês 0 | **0,5857** | **0,8601** |
| previsto médio | 0,0208 | 0,0681 |
| Brier | 0,0661 | 0,0519 |

O "previsto médio" de 2,08% era a fração de linhas classificadas como
positivas, e o Brier de 0,0661 é quase exatamente `p(1-p)` da taxa-base — a
assinatura de um preditor constante, que o achado 5 já tinha registrado como
referência. O AUC de 0,586 é o que se obtém ao calcular AUC sobre valores
binários.

Nada disso levantou um alerta automático. Um modelo com AUC de 0,59 num lote de
drift é exatamente o que se espera ver. O que denunciou foi comparar o **mês 0**
— que é controle, sem mecanismo nenhum aplicado — com o AUC de validação do dia
4 (0,8561). Um lote de controle tem de reproduzir o número conhecido; quando não
reproduz, o problema é do instrumento.

A correção carrega a lição no tipo: `predict_proba` agora recebe um
`ProbabilisticClassifier`, um `Protocol` que exige o método
`predict_proba`. Um wrapper que só ofereça `predict` **não satisfaz o tipo**.

### Bug B — o PSI era estruturalmente incapaz de detectar três features

O PSI cortava 10 bins de quantil no `reference` e então fazia
`edges[0] = -inf; edges[-1] = +inf`. Em colunas dominadas por empates isso
destrói a única fronteira que importa:

| coluna | share do valor modal | arestas de quantil distintas | bins efetivos |
|---|---|---|---|
| `NumberOfTimes90DaysLate` | 94,56% | 2 | **1** |
| `NumberOfTime60-89DaysPastDueNotWorse` | 95,03% | 2 | **1** |
| `income_missing` | 80,41% | 2 | **1** |

Todos os decis do 10º ao 90º caem em zero, `np.unique` colapsa as onze arestas
pedidas em duas, e sobrescrever as pontas com ±inf deixa **um bin só**. Com um
bin, o PSI é identicamente zero: a estatística não pode reportar drift naquela
coluna aconteça o que acontecer. Três das onze features estavam nesse estado, e
duas delas são justamente as que o mecanismo de composição move com defasagem —
o coração da cascata do DAG.

A tabela mostrava `0.000` nas três, em todos os meses. Num relatório de
estabilidade, uma coluna perfeitamente estável não parece bug: parece a
coluna que não driftou.

A correção é adaptativa e o gatilho é a própria degeneração: se os quantis
pedidos não produzem `bins + 1` arestas distintas, a coluna é dominada por
empates e passa a ter **um bin por valor observado**. Para uma coluna de
contagem essa é a divisão honesta de qualquer forma — a pergunta é "quantas
linhas saíram de zero", e nenhuma grade de quantil pergunta isso. As pontas
±inf passaram a ser **anexadas**, nunca escritas sobre uma aresta interna.

Depois da correção, as mesmas colunas: `NumberOfTimes90DaysLate` vai de 0,003
no mês 0 a **0,302** no mês 6, e `NumberOfTime60-89Days` de 0,001 a **0,278** —
e ambas começam a se mover **depois** da faixa de 30-59 dias, que é exatamente a
cascata que o cenário implementa.

### O que aprendi disso

Num sistema cujo trabalho é detectar degradação, um instrumento quebrado e um
sinal verdadeiro têm a mesma aparência. As duas defesas que funcionaram não
foram testes unitários — eram bugs de integração — e sim:

1. **Um lote de controle com resposta conhecida.** O mês 0 existe para medir
   ruído de amostragem, e serviu para achar o bug A porque tinha um valor
   esperado independente.
2. **Desconfiar de um zero perfeito.** `0.000` repetido sete vezes em três
   colunas não é estabilidade, é uma estatística que não está sendo calculada.

---

## 8. A ablação refutou a história causal que eu mesmo desenhei

**Etapa 2 · 2026-09-21 · `reports/simulation/summary.md`**

O cenário de `docs/simulation.md` atribui a **degradação silenciosa** ao
mecanismo 2, a inflação nominal: a renda sobe, o `DebtRatio` cai, o modelo lê
risco menor exatamente quando o cliente fica mais arriscado. A história é
coerente, o DAG é defensável, e a ablação mostra que **ela não é o que
acontece neste modelo**.

### O que a intervenção mostrou

Meses 0 a 6, mesma semente, variando só quais mecanismos estão ligados:

| braço | AUC mês 0 → mês 6 | gap de calibração mês 6 | previsto médio mês 0 → mês 6 |
|---|---|---|---|
| `composition_only` (P(X)) | 0,8601 → 0,8130 | **-0,0028** | 0,0681 → 0,2072 |
| `inflation_only` (medição) | 0,8601 → **0,8658** | **-0,0049** | 0,0681 → 0,0652 |
| `stress_only` (P(y\|X)) | 0,8601 → 0,7917 | **-0,0337** | 0,0681 → 0,0667 |
| `all` | 0,8601 → 0,7779 | -0,0396 | 0,0681 → 0,2050 |

**O gap de calibração vem do mecanismo 3, não do 2.** O braço só-inflação
produz um gap de -0,0049 contra -0,0337 do só-estresse, e seu AUC não cai —
sobe 0,0057, dentro do ruído de ±0,007 medido no dia 4.

A direção prevista pelo DAG está certa: o previsto médio cai de 0,0681 para
0,0652, isto é, o modelo **realmente** passa a ler risco menor sob inflação. A
magnitude é que não existe — 29 pontos-base de probabilidade.

### Por que — e uma correção do próprio achado

A primeira versão desta seção explicava o resultado assim: "inspeção §10 dá a
correlação de `MonthlyIncome` com o alvo como **-0,02**, então inflar em 10% uma
das features mais fracas do modelo não podia produzir muito".

**Essa evidência não vale, e pela terceira vez pelo mesmo motivo.**
`MonthlyIncome` tem desvio **14.483** e máximo **3.008.750** — precisamente o
regime que o achado 4 documentou nos contadores de atraso e o achado 6 na
utilização. Um Pearson calculado aí não mede a relação, mede a cauda. A
conclusão da ablação continua de pé porque ela vem da **intervenção**, não do
coeficiente; o que estava errado era a explicação, que citava uma estatística
contaminada para justificar um resultado obtido por outro caminho.

Medido com estatísticas que a cauda não contamina:

| evidência | `MonthlyIncome` | topo da lista | posição |
|---|---|---|---|
| Pearson com o alvo | -0,0155 | — | *contaminado, não usar* |
| Spearman com o alvo | **-0,0648** | — | 4x o Pearson |
| AUC univariada (discriminação) | **0,5746** | utilização, 0,7770 | 6 de 11 |
| importância por permutação (queda de AUC) | **+0,0029** | utilização, +0,0757 | 7 de 11 |
| ganho do XGBoost | **2,15%** | 90d+, 28,47% | 9 de 11 |

O quadro muda, e a explicação com ele. A renda **não** é uma das features mais
fracas: univariadamente ela discrimina 0,5746, meio da tabela, acima de
`DebtRatio`, `NumberOfDependents` e das duas contagens de carteira. O Spearman é
quatro vezes maior que o Pearson, exatamente como nos achados 4 e 6.

A explicação correta não é sobre a força da variável, é sobre **o quanto este
modelo a usa**: `MonthlyIncome` responde por **2,15%** do ganho do XGBoost, e
embaralhar a coluna inteira custa **0,0029** de AUC. As quatro primeiras
features — os três contadores de atraso e a utilização — concentram **82,07%**
do ganho. Mover 10% uma coluna que responde por 2% do modelo desloca a previsão
média em 29 pontos-base, que é exatamente o que a ablação mediu.

Isso **não** significa que inflação nominal não seja um mecanismo real de drift
por medição. Significa que, neste modelo, ela é pequena — porque este modelo
quase não usa renda. Num scorecard que usasse renda de forma central, a mesma
intervenção daria outro resultado. A afirmação testável não é "inflação causa
degradação silenciosa", é "inflação causa degradação silenciosa **em modelos que
dependem das features que a inflação move**".

### Terceira aplicação da mesma lição

O achado 6 termina com: *"uma justificativa que cita um número precisa citar
também como aquele número foi calculado"*. Esta é a **terceira** vez que a
lição se aplica, e a segunda vez que eu mesmo a violei depois de escrevê-la:

| # | coluna | Pearson | estatística robusta | consequência |
|---|---|---|---|---|
| 4 | contadores de atraso | 0,98 entre si | 0,27 sem as sentinelas | descartaria duas colunas |
| 6 | utilização | -0,0018 | +0,2816 sem a cauda | teto errado, monitor cego |
| 8 | renda | -0,0155 | ganho 2,15%, perm. 0,0029 | explicação errada |

O padrão é estável o bastante para virar regra: **neste dataset, um Pearson
perto de zero não é evidência de ausência de relação — é evidência de que a
coluna tem cauda.** Antes de citar um coeficiente aqui, o cheque é o desvio
contra a média. Três colunas de onze já falharam esse cheque.

### Um resultado nulo que virou um resultado melhor

O mesmo aconteceu no cenário só-multivariado. O par escolhido pela regra
especificada — maior dependência entre colunas que nenhum outro mecanismo toca,
`NumberOfOpenCreditLinesAndLoans` / `NumberRealEstateLoansOrLines` — produziu
**resultado nulo**: ΔAUC de +0,0024, dentro do ruído.

Testar um segundo par mudou a conclusão. O canal de aperto de crédito do DAG
(`utilização` / `idade`) é igualmente invisível ao univariado — PSI máximo de
0,0041 em toda feature — e custa **-0,0168 de AUC**, fora da banda de ruído.

| par | PSI máx | ΔAUC | leitura |
|---|---|---|---|
| `open_lines` / `real_estate` | 0,0041 | +0,0024 | indetectável **e** inofensivo |
| `utilização` / `idade` | 0,0041 | **-0,0168** | indetectável e **danoso** |

Com um par só, "univariado não vê" e "não importa" teriam sido confundidos. São
duas afirmações diferentes, e o dia 8 precisa das duas: uma mede
detectabilidade, a outra mede dano.

### O que aprendi disso

A ablação custou uma função de quatro linhas e um laço. Ela refutou uma
hipótese do desenho, localizou a causa real do único sinal que importa para a
etapa 3, e transformou um resultado nulo em dois resultados. Em produção
nenhuma dessas três coisas seria possível: só o total é observável, e
atribuição passa a ser argumento em vez de medição.

O corolário desconfortável é que a história causal **estava escrita antes** —
como o plano exigia, e com razão — e uma história escrita antes é uma hipótese,
não um resultado. O valor de escrevê-la primeiro é justamente ter algo que a
intervenção pode contradizer.

---

## 9. O 2x2 da etapa 2: drift não é degradação

**Etapa 2 · 2026-09-21 · `reports/simulation/summary.md`**

O resultado mais importante da etapa inteira, e ele cabe em duas linhas da
tabela de ablação lidas lado a lado:

| braço | PSI máx (mês 6) | features 🔴 | gap de calibração | AUC |
|---|---|---|---|---|
| `composition_only` | **0,9327** | 5 de 11 | **-0,0028** | 0,8130 |
| `stress_only` | **0,0082** | **0 de 11** | **-0,0337** | 0,7917 |

**`composition_only` — alarme máximo, calibração intacta.** A composição da
carteira muda: entram clientes mais alavancados, mais jovens, com mais atrasos.
O painel de drift grita — PSI de 0,93 na utilização, cinco features na faixa
vermelha, a inadimplência observada vai de 7,08% a 21,00%. E o modelo continua
**calibrado**: gap de -0,0028, praticamente o mesmo do lote de controle. Faz
sentido, e é o ponto: os rótulos são reais, então clientes mais arriscados de
fato inadimplem mais, e as probabilidades do modelo continuam certas *para
eles*. O modelo não está errado — a população é que é outra.

**`stress_only` — silêncio total, dano máximo.** Nenhuma feature driftou: PSI
máximo de **0,0082**, mais de dez vezes abaixo do limiar de alerta e na mesma
ordem do ruído de amostragem do mês 0 (0,0041). Nenhuma feature em faixa
amarela, quanto menos vermelha. E o gap de calibração chega a **-0,0337**, doze
vezes o do braço anterior. O painel inteiro fica verde enquanto o modelo perde
a noção do nível de risco.

A razão é que este braço muda `P(y|X)` e não toca em `P(X)`: os rótulos viram,
as features não. Não há nada em `X` para um teste de distribuição encontrar,
porque nada em `X` mudou.

> ## Drift não é degradação, e degradação não exige drift.

### A consequência de engenharia

Monitorar drift de features **não pode**, sozinho, pegar o mecanismo que causa a
maior parte do dano de calibração. O braço `stress_only` é invisível a PSI, a KS
por feature, a Wasserstein, a PSI multivariado, a qualquer distância entre
distribuições de entrada — não porque os testes sejam fracos, mas porque a
quantidade que eles medem genuinamente não mudou. Nenhuma vigilância sobre `X`
alcança uma mudança em `P(y|X)`.

O corolário é que **monitoramento baseado em rótulo é obrigatório, não
complementar**: rótulos com atraso, gap de calibração, Brier por lote. É a única
família de sinal que enxerga drift de conceito.

E é também a mais cara e a mais lenta, porque o rótulo chega meses depois — o
que é exatamente o motivo de os lotes serem escritos em três arquivos
separados. Isso faz do drift de features um sinal **antecedente** útil e
insuficiente: ele chega primeiro e às vezes chega sozinho, e um painel que só
tenha ele estará verde no pior cenário dos dois.

Os dois juntos cobrem o quadrado inteiro. Cada um sozinho cobre metade, e as
metades não são as mesmas:

|  | features driftam | features não driftam |
|---|---|---|
| **calibração se mantém** | `composition_only` — monitor de drift acerta o alarme, nada a fazer no modelo | lote de controle (mês 0) |
| **calibração quebra** | `all` — os dois sinais disparam | `stress_only` — **só o rótulo vê** |

O canto inferior direito é o que justifica a etapa 3 inteira.

---

## 10. O PSI do Evidently não tem o nosso bug — tem outro, e ele troca um veredito

**Etapa 2 · 2026-09-22 · `src/credit_monitor/reporting/drift.py`**

O dia 6 descobriu que binning por quantil colapsa em **um bin só** em colunas
94-95% zeradas, tornando o PSI incapaz de reportar drift (achado 7). Antes de
confiar em qualquer número do Evidently, a mesma pergunta foi feita a ele.

### A boa notícia: nas contagens, os dois concordam

`get_binned_data` troca para **um bin por valor** quando a coluna tem 20 ou
menos valores distintos. É exatamente a regra que o dia 6 adotou, e os dois
estimadores concordam em três casas decimais:

| feature | nosso PSI | PSI do Evidently |
|---|---|---|
| `NumberOfTime30-59DaysPastDueNotWorse` | 0,4418 | 0,4441 |
| `NumberOfTimes90DaysLate` | 0,3018 | 0,3019 |
| `NumberOfTime60-89DaysPastDueNotWorse` | 0,2775 | 0,2796 |
| `NumberOfDependents` | 0,0201 | 0,0205 |
| `income_missing` | 0,0134 | 0,0134 |

**O Evidently não tem o bug do achado 7.** Vale registrar isso com o mesmo
destaque de um defeito: a expectativa ao abrir a investigação era encontrar a
mesma falha, e ela não estava lá.

### A má notícia: nas contínuas, um veredito muda

| feature | nosso PSI | Evidently | razão | veredito |
|---|---|---|---|---|
| `RevolvingUtilizationOfUnsecuredLines` | 0,9420 | 0,6486 | 1,45x | 🔴 / 🔴 |
| **`DebtRatio`** | **0,0244 🟢** | **0,1083 🟡** | 0,23x | **discordam** |
| `NumberRealEstateLoansOrLines` | 0,0441 | 0,0132 | 3,34x | 🟢 / 🟢 |
| `MonthlyIncome` | 0,0218 | 0,0159 | 1,37x | 🟢 / 🟢 |

O `DebtRatio` é a coluna que o mecanismo de inflação divide por (1+πs). Os dois
estimadores olham para a mesma mudança real de 9% em 80% das linhas e um diz
"estável", o outro "moderado".

### A causa

Para coluna numérica com mais de 20 valores distintos, o Evidently usa
`np.histogram_bin_edges(combined, bins="sturges")` — bins de **largura igual**
sobre a união de referência e lote.

O problema não é a largura igual em si, é que o resultado **depende do número
de bins**, que é uma escolha arbitrária de configuração:

| coluna | bins | quantil no reference | largura igual |
|---|---|---|---|
| `DebtRatio` | 10 | 0,0244 | 0,0090 |
| `DebtRatio` | 18 | 0,0272 | **0,1676** |
| `DebtRatio` | 30 | 0,0303 | 0,1796 |
| `RevolvingUtilization` | 10 | 0,9420 | 0,1484 |
| `RevolvingUtilization` | 18 | 0,9482 | 0,6432 |
| `RevolvingUtilization` | 30 | 0,9547 | 0,7801 |

Com bins de quantil, dobrar ou triplicar a contagem move o PSI em **menos de
25%**. Com largura igual, o `DebtRatio` varia **20 vezes** entre 10 e 18 bins —
de estável a quase alerta. Um número que oscila assim com um parâmetro que
ninguém pensou em escolher não é uma medida, é uma coincidência.

### `nbinsx` não corrige

`ValueDrift` aceita `nbinsx`, o que parece ser exatamente a alavanca. Não é: o
caminho numérico de `get_binned_data` **ignora** o parâmetro e chama
`bins="sturges"` direto. Medido — 5, 10, 30, 100 e 300 bins devolvem valores
idênticos até a sexta casa:

```
MonthlyIncome  nbinsx 5/10/30/100/300 -> [0.015902, 0.015902, 0.015902, 0.015902, 0.015902]
DebtRatio      nbinsx 5/10/30/100/300 -> [0.10832,  0.10832,  0.10832,  0.10832,  0.10832]
```

### A correção

Registrar um *stattest* próprio e referenciá-lo por nome. `method=` aceita
**só string** na API atual — passar um callable é recusado com erro de
validação —, então registrar é o único caminho:

```python
StatTest(name="reference_psi", ...)
register_stattest(reference_psi_test, _impl)
ValueDrift(column=..., method="reference_psi")
```

A implementação **delega** para `credit_monitor.simulation.psi`, o mesmo código
do dia 6. Isso importa: o número no HTML do Evidently e o número da nossa
tabela passam a ser o mesmo número por construção, não duas estimativas que por
acaso se parecem. Afirmado em teste com tolerância de **1e-12**
(`test_the_custom_stattest_delegates_to_our_psi`).

### Uma segunda propriedade, descoberta ao versionar os HTML

O HTML do Evidently **não é determinístico byte a byte**. Duas gerações do mesmo
dado, com o mesmo código, produzem arquivos de tamanho idêntico (4.222.602
bytes) que diferem em **2.314 posições** — porque a variável JavaScript do
relatório recebe um UUID aleatório a cada execução
(`metric_cfb614652481481f8dc3520daa224f4f` contra
`metric_bf95aaaa900b42ab984ae4bb63cd1981`). Não são timestamps: nenhuma data
difere entre as duas.

O conteúdo é o mesmo, o arquivo não. Consequência prática: gerar relatórios
direto no diretório versionado faria de **toda** regeneração de rotina um diff
de seis arquivos de 4 MB com números idênticos dentro. Por isso
`make drift-reports` escreve em `reports/evidently/_build/` (ignorado) e
`make publish-reports` é o único alvo que toca HTML versionado.

### Uma ressalva honesta

O argumento contra largura-igual **não** é que os bins mudam de mês para mês.
Essa era a hipótese, e ela foi testada e **refutada**: as arestas ficaram
idênticas nos sete meses, porque o pré-processamento limita `DebtRatio` a 2 e a
utilização a 10, e a máxima da união é dominada pela referência. A instabilidade
é em relação ao **número de bins**, não ao lote — e isso basta.

### O que aprendi disso

Dois estimadores da mesma estatística, sobre os mesmos dados, discordando o
suficiente para trocar um veredito é o tipo de coisa que só aparece quando
alguém põe os dois lado a lado. O cross-check custou uma tabela; sem ele, o
projeto teria dois painéis — o nosso `summary.md` e o HTML do Evidently —
dizendo coisas diferentes sobre `DebtRatio`, e a descoberta viria de alguém
apontando a discrepância numa apresentação.

---

## 11. A afirmação sobre o KS estava errada — o problema é outro

**Etapa 2 · 2026-09-22 · `src/credit_monitor/drift_tests/aa.py`**

O plano vinha repetindo que "o KS gera falso alarme com amostra grande". O teste
A/A — 200 amostras do holdout contra a referência completa, sete tamanhos, sem
drift algum — mostra que **isso é falso**.

### A taxa por feature não cresce com n

| tipo de feature | n = 250 | n = 1.000 | n = 7.000 | n = 20.000 |
|---|---|---|---|---|
| contínuas (4) | 2,9% | 5,1% | 5,9% | 6,2% |
| contagens (7) | 0,4% | 0,6% | 0,3% | 0,6% |

Sob o nulo verdadeiro os p-valores do KS são uniformes, então a taxa por
feature fica em **alfa para qualquer n** — é o que as contínuas fazem, orbitando
0,05 do menor ao maior tamanho. Nas contagens ela fica de cinco a dez vezes
**abaixo** de alfa, porque os empates tornam o KS conservador: nos três
contadores de atraso a taxa é exatamente **0,000 em todos os tamanhos**.

### Os dois problemas reais

**Múltiplas comparações.** Onze features a 5% fazem o painel inteiro acender em
**12% a 25%** dos lotes limpos, e isso quase não depende de n:

| n | KS sem correção | KS + Bonferroni | KS + BH |
|---|---|---|---|
| 250 | 13,5% | 0,5% | 0,5% |
| 1.000 | 22,0% | 3,0% | 3,0% |
| 7.000 | **21,5%** | 1,5% | 1,5% |
| 20.000 | 24,5% | 1,5% | 1,5% |

Bonferroni e Benjamini-Hochberg **coincidem em todos os tamanhos**. Não é
coincidência numérica: sob o nulo raramente há mais de um p-valor pequeno, e aí
o corte do BH no primeiro posto (`alfa/11`) é o próprio Bonferroni.

**Significância não é magnitude.** Varrendo só o mecanismo de inflação:

| π | n | p-valor KS (renda) | PSI | Δ previsão média | Δ AUC |
|---|---|---|---|---|---|
| 0,01 | 1.000 | 2,0e-01 | 0,0057 | -0,00036 | +0,00104 |
| 0,01 | 44.000 | **6,0e-11** | 0,0015 | -0,00035 | -0,00032 |
| 0,10 | 44.000 | **1,2e-80** | 0,0209 | -0,00151 | +0,00073 |

**O mesmo deslocamento** de 1% na renda é invisível ao KS a n = 1.000
(p = 0,20) e tem p = 6e-11 a n = 44.000. O que mudou não foi o efeito — a
previsão média se move 3,5 pontos-base nos dois casos — foi o tamanho do lote.
É alarme verdadeiro e operacionalmente inútil.

### O PSI falha no regime oposto, e de forma prevista

| n | PSI médio medido | previsto `(bins-1)(1/n+1/m)` | razão |
|---|---|---|---|
| 250 | 0,0412 | 0,0437 | 0,94 |
| 1.000 | 0,0115 | 0,0110 | 1,04 |
| 7.000 | 0,0019 | 0,0017 | 1,13 |
| 20.000 | 0,0008 | 0,0007 | 1,21 |

O viés de pequena amostra não é mistério: bate com a aproximação qui-quadrado
dentro de 6% nos tamanhos pequenos. A **taxa de falso alarme do PSI > 0,10 vai
a 28,5% a n = 250** e some a partir de n = 1.000.

E quem paga são as colunas com mais bins — ou seja, **é o preço da correção do
dia 6**. A regra de um bin por valor consertou a cegueira do PSI nas colunas
94% zeradas e, em troca, deu a elas 17 e 26 bins:

| feature | disparos a n=250 | bins | viés previsto |
|---|---|---|---|
| `NumberOfTimes90DaysLate` | 12,0% | 17 | 0,0642 |
| `NumberRealEstateLoansOrLines` | 10,5% | 26 | **0,1002** |
| `NumberOfTime30-59DaysPastDueNotWorse` | 7,5% | 13 | 0,0481 |

O viés previsto da segunda linha **é** o limiar de alerta. Uma correção que
resolve um problema num regime e cria outro no regime oposto.

### Recomendação operacional (n ≈ 7.000)

| regra | falso alarme | veredito |
|---|---|---|
| KS sem correção | 21,5% | inutilizável como portão |
| KS + Bonferroni | 1,5% | responde à pergunta errada |
| PSI > 0,10 | **0,0%** | alerta |
| PSI > 0,25 | **0,0%** | bloqueio |

**PSI decide o veredito; KS fica como diagnóstico, sempre corrigido.** Zero
falsos alarmes em 200 sorteios limita a taxa real a menos de 1,5% com 95% de
confiança. E o limiar **não é transportável**: abaixo de n ≈ 500 a ordem se
inverte e o PSI vira o pior dos dois.

### O que aprendi disso

A afirmação errada durou semanas porque era **plausível** — "amostra grande
detecta tudo" soa certo e tem um fundo de verdade (o problema da magnitude). O
que ela errava era o mecanismo, e errar o mecanismo leva à correção errada: com
ela, alguém aumentaria o alfa em lotes grandes, que é exatamente a mudança que
não resolve nada. O A/A custou dois minutos de cálculo.

---

## 12. MMD vê o drift que nenhuma marginal revela

**Etapa 2 · 2026-09-22 · `src/credit_monitor/drift_tests/mmd.py`**

Os dois lotes só-multivariados da etapa 2 têm **toda marginal idêntica por
construção** (a dependência de um par foi invertida por permutação) e PSI
univariado de **0,0041** em todas as onze features — verde com duas ordens de
grandeza de folga. Nenhum teste univariado pode vê-los, não por fraqueza, mas
porque não há nada nas marginais para ver.

| lote | MMD² | p | detecta |
|---|---|---|---|
| mês 0 (controle) | -0,000076 | 0,651 | **não** ✓ |
| multivariado primário | 0,003463 | ≤ 0,001 (piso) | **SIM** |
| multivariado aperto de crédito | 0,000922 | ≤ 0,001 (piso) | **SIM** |
| mês 6 (sanidade) | 0,064634 | ≤ 0,001 (piso) | SIM |

Os três "≤ 0,001" são o **piso do teste**, não um valor exato: com 1.000
permutações o menor p atingível é 1/1001, porque a estatística observada conta
como um sorteio do nulo. O teste diz "no máximo isso"; quanto menor, ele não
tem como saber. Escrever `p = 0,001` inventaria precisão que o método não tem,
e convidaria alguém a comparar dois resultados no piso como se um fosse
evidência mais forte.

O controle **não** é detectado, que é a condição mínima para o resto valer. E o
próprio MMD passou por um A/A: **4,0%** de falso alarme em 100 repetições, com
p-valores uniformes (quartis 0,25 / 0,50 / 0,75). Calibrado.

### Localização: MMD diz *que* mudou, não *onde*

MMD é um número só sobre onze dimensões. Rodá-lo por par recupera a metade que
falta, e nos dois lotes o par invertido sai em **primeiro**:

| lote | par no topo | MMD² | posição do par invertido |
|---|---|---|---|
| primário | `open_lines` / `real_estate` | 0,026 | **#1 de 55** |
| aperto de crédito | `utilização` / `idade` | 0,010 | **#1 de 55** |

No primário, os pares seguintes também envolvem `NumberRealEstateLoansOrLines`:
permutar uma coluna muda a dependência dela com **todas** as outras, não só com
a parceira. A localização aponta para a coluna tanto quanto para o par — o que é
informação útil, não ruído.

### O MMD ordena por tamanho estatístico, não por dano

Este é o alerta mais importante da seção, e ele aparece nos próprios números
acima. Os dois lotes só-multivariados são casos diferentes — um é inofensivo, o
outro machuca — e o MMD os ordena **ao contrário**:

| lote | Spearman original do par | MMD² | Δ AUC no campeão |
|---|---|---|---|
| primário (`open_lines` / `real_estate`) | 0,464 | **0,003463** | **+0,0024** (inofensivo) |
| aperto de crédito (`utilização` / `idade`) | 0,275 | 0,000922 | **-0,0168** (danoso) |

O lote **inofensivo** tem MMD² **3,8x maior** que o danoso. E a razão é
mecânica, não acidental: a dependência original do primeiro par é mais forte
(Spearman 0,464 contra 0,275), então invertê-la desloca mais massa da
distribuição conjunta. O MMD mede exatamente esse deslocamento — e deslocamento
não é dano.

É o 2x2 de novo, em forma multivariada. O achado §9 mostrou que drift de
feature e degradação de modelo são eixos separados; aqui, **magnitude de drift
multivariado e degradação também são**. Um detector responde "o quanto a
distribuição mudou", e quem opera precisa de "o quanto isso me custa" — são
perguntas diferentes e nenhum teste estatístico responde a segunda.

O mesmo princípio já tinha aparecido no dia 7, no ranking por **PSI x ganho**:
`age` driftou mais que `NumberOfTimes90DaysLate` (PSI 0,314 contra 0,302) e vale
um oitavo do impacto, porque o modelo se apoia 3,29% na idade e 28,47% no
contador. A correção é a mesma nos dois casos: **pondere o sinal do detector
por alguma medida de quanto o modelo depende daquilo**, ou ordene por dano
medido, e nunca apresente a magnitude estatística como prioridade.

### Três escolhas que decidiram o resultado

**Escores de posto, não valores brutos.** O kernel é função de distância; uma
coluna com desvio 249,76 dominaria toda distância par a par e o teste mediria só
ela. É a lição dos achados §4, §6 e §8 aplicada a kernels.

**Largura de banda pela mediana, na referência.** Muito menor que as distâncias
típicas e o kernel vira a identidade; muito maior e todo ponto parece igual.

**Permutações a partir do kernel pré-computado.** `a'Ka` para todas as
permutações de uma vez é um produto de matrizes; recomputar o kernel mil vezes
seria mil vezes o trabalho. Quatro testes de 1.000 permutações com n = 2.000 por
lado custam **1,1 s**.

### Um detalhe que um teste ingênuo esconderia

"MMD de uma amostra contra ela mesma é zero" é falso para o estimador não
enviesado. Ele tira a diagonal dos termos internos mas o termo cruzado mantém
todos os pares, então com X = Y os auto-pares sobrevivem só no cruzado e o
MMD² cai perto de **-2/n**. O teste está no repositório afirmando esse offset,
porque a versão ingênua falharia e pareceria bug no kernel. O caso que de fato
tem de dar zero é **duas metades disjuntas** da mesma população.

---

## 13. A janela cega: dois meses degradando sem sinal de nenhum tipo

**Etapa 3 · 2026-09-22 · `src/credit_monitor/monitoring/blind_window.py`**

Um monitor é caracterizado por dois números, e o segundo quase nunca é
reportado. O primeiro é o quanto ele detecta. O segundo é **por quanto tempo
ele está errado** — o intervalo em que o modelo já está degradando e nada que o
operador possa ver diz isso.

O intervalo existe porque as duas famílias de sinal chegam em momentos
diferentes. Drift de feature está disponível no instante em que o lote é
pontuado. Desempenho precisa do desfecho, que chega **dois meses depois**
(`label_lag_months`, em `configs/monitoring.yaml`). Então uma degradação que
mexe nas features é visível na hora; uma que não mexe fica invisível até os
rótulos chegarem, por pior que seja.

### Medido, com atraso de 2 meses e degradação real definida como gap ≤ -0,015

| cenário | meses degradados | 1º degradado | 1º sinal | **meses cego** |
|---|---|---|---|---|
| `full` | 3, 4, 5, 6 | 3 | **3** | **0** |
| `composition_only` | nenhum | — | — | 0 |
| `stress_only` | 3, 4, 5, 6 | 3 | **5** | **2** |

**`full` tem janela cega zero** porque o drift de feature já está disparando no
mês 3 — os dois mecanismos andam juntos, e o mais rápido dos dois avisa.

**`composition_only` nunca degrada.** Zero aqui não é mérito do monitor: não há
o que ver. O braço grita no painel de drift (PSI 0,93) e mantém o gap em
-0,0028. Vale distinguir "não fiquei cego" de "não havia nada".

**`stress_only` fica dois meses cego.** A degradação começa no mês 3 — gap de
-0,0171, já além do limiar — e nenhuma feature se move: PSI máximo de 0,008,
doze vezes abaixo do limiar de alerta. O único sinal possível é o rótulo, e o
rótulo do mês 3 só chega no passo 5. Durante os meses **3 e 4** o modelo está
medidamente pior e **todo painel está verde**.

### Por que o número é exatamente o atraso

Não é coincidência: quando nenhuma feature se move, a janela cega **é** o atraso
de rótulo, por construção. O sinal de drift nunca chega, então o primeiro sinal
é o rótulo, então a cegueira dura exatamente o tempo que o rótulo leva. O teste
`test_the_blind_window_equals_the_label_lag_when_no_feature_drifts` fixa isso, e
`test_a_longer_lag_widens_the_blind_window` fixa a consequência: **encurtar o
atraso de rótulo é a única alavanca que reduz a janela cega para drift de
conceito puro.** Nenhum limiar, nenhum teste estatístico e nenhuma feature
adicional encurtam — só receber o desfecho mais cedo.

Isso reordena o que vale investir. Melhorar o detector de drift de features não
move este número em nada; conseguir rótulo parcial em um mês em vez de dois o
corta pela metade.

### O que aprendi disso

O 2x2 do achado §9 dizia *que* o monitoramento de features não vê drift de
conceito. Este achado diz **quanto isso custa**, na unidade em que a pergunta é
feita numa reunião: meses. Uma afirmação qualitativa sobre cobertura virou um
prazo, e prazo é a forma que a conversa toma quando alguém pergunta "e se
acontecer?".
