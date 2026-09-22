# Governança, privacidade e LGPD

**Etapa 4 · Lei nº 13.709/2018 (LGPD)**

---

## 0. O escopo deste documento, antes de qualquer afirmação

### 0.1 Por que a LGPD não incide aqui — e por quais motivos ela **não** deixa de incidir

A LGPD se aplica ao tratamento de **dado pessoal**, definido no Art. 5, I como
"informação relacionada a pessoa natural identificada ou identificável". O Art.
12 *caput* fecha o raciocínio na outra ponta: **dados anonimizados não são
considerados dados pessoais** para os fins da lei, desde que o processo de
anonimização não possa ser revertido com esforço razoável.

É exatamente esse o caso deste repositório. O *Give-Me-Some-Credit* (OpenML id
45577) chega **já anonimizado na origem**: não há nome, documento, contato, e a
inspeção do dia 1 confirmou que **não há sequer coluna de identificador**
(`reports/inspection.md` §7). As 11 colunas são atributos de um caso de crédito,
e não há chave — nem direta, nem custodiada em outro lugar — que ligue uma linha
a uma pessoa natural. Não existindo titular identificado nem identificável, não
existe dado pessoal; não existindo dado pessoal, o Art. 12 põe o tratamento fora
do alcance da lei.

Esse é o único motivo. Vale dizer explicitamente **quais motivos não valem**,
porque são os dois atalhos mais comuns e os dois são errados:

- **Não é porque o dado é público.** O Art. 7, §3 é expresso: o tratamento de
  dados tornados manifestamente públicos pelo titular **preserva** os direitos
  do titular e os princípios da lei. Publicidade não apaga proteção. Um dataset
  de nomes e CPFs raspado de um diário oficial continua sendo dado pessoal, e
  tratá-lo continua exigindo base legal.
- **Não é porque o projeto é acadêmico.** O Art. 4, II, *b* dispensa a aplicação
  da lei para tratamento realizado para fins **exclusivamente** acadêmicos, e
  ainda assim remete aos Arts. 7 e 11 para as hipóteses de tratamento. É uma
  porta estreita, condicionada à exclusividade da finalidade, e não é a porta
  por onde este projeto passa. O que o coloca fora da lei é a ausência de
  titular, não a finalidade de quem o construiu.

A diferença importa porque as duas justificativas erradas **não sobrevivem à
mudança de contexto** e a certa não precisa: se amanhã este mesmo pipeline
receber uma proposta de crédito de uma pessoa real, o tratamento passa a ser de
dado pessoal no mesmo instante, sem que uma linha de código mude. Foi o dado que
mudou, não o sistema.

### 0.2 A consequência: por que tudo aqui é dito em duas camadas

Se a lei não incide sobre o repositório como ele está, um documento de
governança tem duas saídas. A primeira é declarar conformidade com obrigações
que ninguém tem — descrever com ar solene o encarregado de dados e o canal do
titular de um trabalho de pós-graduação — e isso é inventar conformidade. A
segunda, que é a adotada aqui, é descrever **o regime que se aplicaria ao mesmo
pipeline rodando sobre candidatos reais**, e marcar em cada afirmação de que
lado da fronteira ela está.

Daí as duas camadas, que atravessam o documento inteiro:

| rótulo | significado |
|---|---|
| **[IMPLEMENTADO]** | o que este repositório faz **hoje**, com arquivo, função, configuração ou teste que se pode abrir e conferir |
| **[PRESCRITO]** | o que o mesmo sistema precisaria **além disso** no dia em que recebesse dado pessoal real, e que aqui **não existe** |

As duas nunca se misturam numa mesma frase. Onde o projeto não faz algo, está
escrito que não faz.

Um aviso sobre o peso relativo: a camada [PRESCRITO] é maior. Isso é o resultado
esperado, não um defeito escondido — a maior parte da conformidade com a LGPD é
processo organizacional (encarregado, canal do titular, contrato com operador,
resposta a incidente), e processo organizacional não mora num repositório de
código. O que mora aqui é a parte técnica, e é sobre ela que as afirmações
[IMPLEMENTADO] respondem.

Uma observação sobre o que a fronteira **não** protege. O fato de o tratamento
estar fora da lei hoje não torna as escolhas de desenho indiferentes: a
quarentena guarda linhas inteiras (§3.1), a referência guarda linhas em vez de
estatísticas (§3.2), o sal do hash é público (§5). Nenhuma dessas é uma
infração, porque não há dado pessoal para infringir — e todas as três seriam
exposições reais no primeiro dia em que houvesse. É por isso que estão descritas
em detalhe, com medição, e não apenas mencionadas.
---

## 1. Inventário de dados

### 1.1 Não existe identificador direto

O dataset **não tem coluna de identificador**. Isso foi verificado, não suposto:
a inspeção do dia 1 procurou explicitamente por `id`, `index`, `row_id`, `rowid`
e `unnamed: 0` e não encontrou nenhuma — está registrado em
[`reports/inspection.md`](../reports/inspection.md) §7. As 11 colunas são todas
atributos do caso.

**O índice da linha não é uma pessoa.** Ele é a posição no arquivo, atribuída
pela ordem de leitura do parquet. Duas execuções do mesmo pipeline sobre o mesmo
arquivo dão o mesmo índice à mesma linha, e é só isso que ele significa. Ele
aparece no pipeline como chave de junção — a quarentena precisa dele para dizer
*qual* linha foi rejeitada por *qual* regra ([`pipeline/ingest.py`](../src/credit_monitor/pipeline/ingest.py),
`quarantine_rows`) — e viaja para os logs já com hash (§5).

### 1.2 As 11 colunas

Nenhuma coluna é **dado pessoal sensível** no sentido do Art. 11: não há origem
racial ou étnica, convicção religiosa, opinião política, filiação sindical, dado
referente à saúde, à vida sexual, genético ou biométrico. A classificação abaixo
é a que valeria **num sistema real**, onde as mesmas colunas estariam ligadas a
um titular identificado.

| # | coluna | pessoal (Art. 5, I), num sistema real | sensível (Art. 11) | o que revela sobre a pessoa | tratamento neste projeto |
|---|---|---|---|---|---|
| 1 | `SeriousDlqin2yrs` | **sim** — alvo | não | inadimplência grave em 2 anos: o desfecho financeiro da pessoa | rótulo; chega com 2 meses de atraso (`label_lag_months`) e vive em `labels.parquet` |
| 2 | `RevolvingUtilizationOfUnsecuredLines` | **sim** | não | quanto do limite rotativo está usado — aperto financeiro | feature; teto de 10,0 em `PreprocessingParams.utilization_cap` |
| 3 | `age` | **sim** | não | idade. Quase-identificador clássico e critério protegido | feature; contrato rejeita fora de [18, 110] (`age_range`) |
| 4 | `NumberOfTime30-59DaysPastDueNotWorse` | **sim** | não | histórico de atraso leve | feature; contrato rejeita sentinelas 96/98 |
| 5 | `DebtRatio` | **sim** | não | razão dívida/renda — comprometimento de renda | feature; teto de 2,0 em `debt_ratio_cap` |
| 6 | `MonthlyIncome` | **sim** | não | renda mensal. O campo mais sensível do conjunto em termos práticos | feature; nulo permitido, imputado, **com flag `income_missing`** |
| 7 | `NumberOfOpenCreditLinesAndLoans` | **sim** | não | quantas linhas de crédito a pessoa mantém abertas | feature |
| 8 | `NumberOfTimes90DaysLate` | **sim** | não | histórico de atraso grave | feature; contrato rejeita sentinelas 96/98 |
| 9 | `NumberRealEstateLoansOrLines` | **sim** | não | financiamentos imobiliários — proxy de patrimônio | feature |
| 10 | `NumberOfTime60-89DaysPastDueNotWorse` | **sim** | não | histórico de atraso intermediário | feature; contrato rejeita sentinelas 96/98 |
| 11 | `NumberOfDependents` | **sim** | não | dependentes. Proxy de composição familiar | feature; contrato rejeita negativo |

Uma nota sobre a coluna 6: o pipeline cria `income_missing`
([`data/preprocess.py`](../src/credit_monitor/data/preprocess.py)), uma feature
derivada que registra **que a renda estava ausente**. Num sistema real isso é
dado pessoal por direito próprio — "esta pessoa não informou renda" é uma
informação sobre a pessoa, e o modelo a usa. Ela existe porque a ausência é
informativa; ela precisa entrar no inventário pelo mesmo motivo.

### 1.3 Reidentificação: o risco honesto

Nenhuma coluna identifica sozinha. Mas **`age` + `MonthlyIncome` +
`NumberOfDependents` + `NumberOfOpenCreditLinesAndLoans` é uma combinação
quase-identificadora**: idade exata, renda exata, número de dependentes e número
de linhas abertas, cruzados, singularizam com facilidade dentro de uma base
municipal ou de um cadastro de cliente. É o mecanismo padrão de reidentificação —
nenhum campo é o culpado, a combinação é.

Aqui isso é inofensivo, porque não há a quem reidentificar: o dataset já veio
sem titular. **Num sistema real seria a exposição principal do conjunto**, e a
mitigação está em [PRESCRITO] abaixo, não em nenhuma coisa que este repositório
faça.

> **[PRESCRITO]** — mitigação de reidentificação num sistema real: faixas em vez
> de valores exatos para idade e renda em qualquer artefato que saia do
> perímetro de produção (relatório, extração analítica, dataset de treino
> compartilhado); avaliação formal de *k*-anonimato na combinação
> quase-identificadora antes de qualquer compartilhamento; e controle de acesso
> por papel sobre a base de features, separando quem treina de quem opera. Nada
> disso existe neste repositório.

---

## 2. Base legal para decisão automatizada de crédito

### 2.1 A base: proteção ao crédito (Art. 7, X)

A hipótese aplicável a *credit scoring* no Brasil é a **proteção do crédito**,
do Art. 7, inciso X — a mesma que sustenta birôs de crédito e a que o material
do curso lista para este caso. Ela é a base correta aqui, e vale explicar por
que as duas alternativas óbvias são piores.

**Consentimento (Art. 7, I) é o encaixe mais fraco dos três**, por dois motivos
que se somam:

1. **Não é livre.** O Art. 5, XII exige manifestação livre, informada e
   inequívoca. Um consentimento que é condição para obter crédito não é livre em
   nenhum sentido útil da palavra: a pessoa que precisa do empréstimo assina o
   que for. Consentimento obtido sob necessidade é consentimento de fachada.
2. **É revogável, e a revogação quebra a própria base do modelo.** O Art. 8, §5
   garante revogação a qualquer momento. Se o tratamento se apoia em
   consentimento, cada revogação obriga a expurgar aquele titular — inclusive do
   conjunto de treino e da referência de drift. Um modelo cuja base de
   comparação pode ser esvaziada por decisão individual dos titulares não tem
   base de comparação: a referência da etapa 1 deixaria de ser um retrato fixo
   da população e viraria um alvo móvel, e toda medição de drift contra ela
   perderia sentido. É uma fragilidade **estrutural**, não operacional.

**Legítimo interesse (Art. 7, IX)** é defensável, mas é a base de exceção: exige
teste de proporcionalidade documentado, cede diante de direitos e liberdades
fundamentais do titular, e é atacável caso a caso. Quando existe um inciso
específico para a finalidade — e existe, o X — usar o genérico é escolher o
terreno mais frágil sem ganhar nada.

> **[PRESCRITO]** — o registro das operações de tratamento (Art. 37), o teste de
> proporcionalidade documentado e o relatório de impacto à proteção de dados
> (Art. 38), quando exigido pela ANPD, são documentos organizacionais. **Não
> existem neste repositório e não poderiam existir**, porque pressupõem um
> controlador real com titulares reais.

### 2.2 Art. 20: revisão e informação sobre os critérios

O Art. 20 dá ao titular o direito de **solicitar a revisão** de decisões tomadas
unicamente com base em tratamento automatizado que afetem seus interesses — e
o §1 dá o direito a **informações claras e adequadas sobre os critérios e os
procedimentos** utilizados, respeitado o segredo comercial e industrial.

Um pedido de revisão precisa de quatro coisas: saber **qual modelo** decidiu,
**sobre qual dado**, **quais critérios** ele usa, e **que a decisão é
auditável**. O projeto entrega as quatro em nível de sistema:

| o que o Art. 20 exige | **[IMPLEMENTADO]** — onde está |
|---|---|
| qual modelo decidiu | MLflow Model Registry com alias `champion` e versão registrada; cada execução de monitoramento grava as tags `champion_model`, `champion_alias` e `champion_version` ([`monitoring/run.py`](../src/credit_monitor/monitoring/run.py), `_record_mlflow`) |
| sobre qual dado | `mlflow.log_input` com o dataset e a tag `dataset_sha256` do arquivo exato de treino ([`models/train.py`](../src/credit_monitor/models/train.py)); o SHA-256 do bruto está fixado em `constants.py` e verificado a cada download |
| quais critérios, em nível de modelo | participação de cada feature no ganho do campeão, extraída do próprio modelo (`champion_gain_shares` em [`reporting/drift.py`](../src/credit_monitor/reporting/drift.py)) e publicada como a métrica `feature_gain_share` — é a mesma quantidade que decide a severidade do alerta de drift |
| que o número signifique algo | a probabilidade é **calibrada** por isotônica ajustada numa partição separada; o campeão registrado é o calibrado (achado §5 em [`findings.md`](findings.md)) |
| rastro de auditoria | **toda** execução de monitoramento é um run registrado no MLflow, com os parâmetros do lote, as métricas medidas e o veredito — e apenas o que foi medido: um lote bloqueado não recebe zeros fabricados |

O quarto item da tabela é o que costuma faltar e é o que mais importa para o
titular. Um score é um número que decide a vida de alguém; se esse número não
significa o que diz, informar "o critério foi um score de 0,42" é informar nada.
O achado §5 do projeto mediu exatamente esse buraco: reponderar a classe para
tratar o desbalanceamento infla as probabilidades por um fator de ~14, sem mexer
no AUC nem no KS. Um painel que acompanhasse só ordenação jamais veria. Voltar
para §4 (Privacy by Design, respeito ao usuário) para a leitura de justiça disso.

> **[PRESCRITO]** — **explicação por decisão individual não está implementada.**
> O projeto explica o **modelo** (quais features pesam, no agregado); ele não
> explica **a decisão sobre uma pessoa** (por que *esta* proposta foi negada).
> As duas coisas são diferentes e o Art. 20 §1 pede a segunda. Um sistema real
> precisaria de atribuição por instância — SHAP ou equivalente — persistida
> junto da predição, de um canal formal de pedido de revisão com prazo de
> resposta, e de **revisão por pessoa humana** com autoridade para reverter a
> decisão automatizada. Nada disso existe aqui.

> **[PRESCRITO]** — o Art. 20 §2 permite à ANPD auditar os aspectos
> discriminatórios do tratamento. A resposta a uma auditoria dessas é
> organizacional e não existe neste repositório.

### 2.3 Não discriminação (Art. 6, IX) — etapa 5

O Art. 6, IX veda o tratamento para fins discriminatórios ilícitos ou abusivos.
Este documento **não** avalia viés do modelo: **isso é o escopo da etapa 5**, que
mede disparidade por grupo. O que cabe registrar aqui é o ponto de partida
honesto: `age` é uma feature do modelo e é um critério protegido, e o modelo é
treinado sobre desfechos históricos — se a concessão de crédito que gerou esses
rótulos foi enviesada, o modelo aprende o viés e o chama de padrão.

---

## 3. Retenção e ciclo de vida

Os prazos abaixo são **propostos**, com a razão ao lado. Dois têm evidência no
repositório e estão marcados; o resto é [PRESCRITO] — uma política de retenção é
uma decisão do controlador, não uma constante de código.

| artefato | onde | o que guarda | teria dado pessoal num sistema real? | retenção proposta | razão | descarte |
|---|---|---|---|---|---|---|
| dado bruto | `data/raw/gmsc.parquet` | as 150.000 linhas originais | **sim** — todas as 11 colunas | enquanto durar a finalidade + prazo legal de crédito (5 anos, Art. 43 CDC) | é a origem de tudo; sem ele nada se reproduz | exclusão do objeto + rotação de backup |
| referência (etapa 1) | `data/processed/reference.parquet` | partição de treino/calibração/validação | **sim**, hoje linha a linha | ver §3.2 — **linhas não sobrevivem, estatísticas sim** | é a linha de base do drift | substituição por agregados |
| holdout | `data/processed/holdout.parquet` | partição intocada de avaliação | **sim** | mesma regra da referência | fonte das distribuições nulas dos limiares | idem |
| lotes de produção | `data/production/<mês>/features.parquet` | features do lote pontuado | **sim** | 12 meses | precisa cobrir o atraso de rótulo (2 meses) com folga para investigar | exclusão do arquivo |
| predições | `data/production/<mês>/predictions.parquet` | probabilidade por linha | **sim** — é a decisão sobre a pessoa | **5 anos** | é o registro da decisão; sem ela não há como atender pedido de revisão do Art. 20 | exclusão após o prazo |
| rótulos | `data/production/<mês>/labels.parquet` | desfecho observado | **sim** | 5 anos | pareia com a predição; separado dela porque chega 2 meses depois | idem |
| **quarentena** | `data/quarantine/<lote>/` | `rows.parquet` (linhas rejeitadas **íntegras**) + `rejections.jsonl` (com `observed_value`) | **sim, e é o caso mais delicado** | **90 dias** | ver §3.1 | exclusão do diretório |
| logs estruturados | `logs/*.jsonl` | contagens, durações, status, ids **com hash** | **não** — nenhum valor de feature, por construção (§4) | 90 dias | janela de investigação operacional | rotação de arquivo |
| métricas Prometheus | volume do container | séries agregadas por lote | **não** — agregado, sem linha | **15 dias** · **[IMPLEMENTADO]** | `--storage.tsdb.retention.time=15d` em `docker-compose.monitoring.yml:71` | expurgo automático do TSDB |
| logs no Loki | volume do container | o que os JSONL carregam | **não** | 90 dias | acompanha a retenção do JSONL de origem | ver a limitação abaixo |
| runs do MLflow | `mlruns/` + `mlflow.db` | parâmetros, métricas, tags, hash do dataset | **não** — agregado e metadado | **5 anos** | é o rastro de auditoria do Art. 20; tem de durar o que dura a decisão | exclusão de experimento |
| relatórios HTML | `reports/evidently/` | histogramas e estatísticas de distribuição | **não** — verificado: os payloads são histogramas e agregados, não linhas | 12 meses | evidência de drift ao longo do tempo | exclusão do arquivo |

> **[PRESCRITO]** — todos os prazos da tabela, **menos os 15 dias do
> Prometheus**, são propostas escritas aqui e **não estão aplicadas em lugar
> nenhum**: não há job de expurgo, não há política de ciclo de vida de objeto,
> não há rotação automatizada. Um sistema real precisaria de cada prazo
> executado por um mecanismo, não por um documento.

> **[PRESCRITO]** — **o Loki desta stack não tem retenção configurada.**
> [`monitoring/loki-config.yml`](../monitoring/loki-config.yml) não define
> `retention_period` nem habilita o compactor com `retention_enabled: true`: na
> prática os logs ficam para sempre. Como esses logs não carregam valor de
> feature (§4), o impacto de privacidade aqui é baixo — mas a afirmação "logs
> ficam 90 dias" seria **falsa** hoje, e é por isso que ela está nesta camada e
> não na outra. O mesmo arquivo tem `auth_enabled: false`, e nem Loki nem
> Prometheus nem Grafana exigem autenticação nesta stack de avaliação local.

### 3.1 A quarentena é a tensão mais aguda do documento

A minimização (Art. 6, III) manda tratar o mínimo necessário. A quarentena faz o
contrário de propósito: quando o contrato rejeita uma linha, ela é gravada
**inteira e como chegou** em `rows.parquet`, e o valor exato que causou a rejeição
vai para `rejections.jsonl` no campo `observed_value`
([`pipeline/ingest.py`](../src/credit_monitor/pipeline/ingest.py),
`quarantine_rows`).

Isso é dado pessoal preservado exatamente onde a minimização pediria descarte.

### O que está realmente escrito lá — conferido, não suposto

Esta seção é o argumento mais delicado do documento, então o conteúdo da
quarentena foi **aberto e medido**, não deduzido do código. Lote
`data/quarantine/batch_20260920/`:

**`rows.parquet` — 12 linhas × 11 colunas.** Todas as colunas, sem exceção:

```
SeriousDlqin2yrs · RevolvingUtilizationOfUnsecuredLines · age ·
NumberOfTime30-59DaysPastDueNotWorse · DebtRatio · MonthlyIncome ·
NumberOfOpenCreditLinesAndLoans · NumberOfTimes90DaysLate ·
NumberRealEstateLoansOrLines · NumberOfTime60-89DaysPastDueNotWorse ·
NumberOfDependents
```

Isso inclui a combinação quase-identificadora inteira da §1.3 — `age`,
`MonthlyIncome`, `NumberOfDependents` e `NumberOfOpenCreditLinesAndLoans`
juntas, com os valores exatos — **e também o rótulo** `SeriousDlqin2yrs`, que é
o desfecho financeiro da pessoa. O índice `batch_row_index` é preservado e
nomeado, porque é a chave de junção de volta para o JSONL.

**`rejections.jsonl` — 13 registros, e 8 deles carregam um valor de feature em
claro** no campo `observed_value`:

| regra | coluna | `observed_value` | registros |
|---|---|---|---|
| `age_range` | `age` | `12` | 3 |
| `monthly_income_non_negative` | `MonthlyIncome` | `-1500.0` | 2 |
| `delinquency_sentinels` | `NumberOfTimes90DaysLate` | `98` | 2 |
| `schema_columns_and_dtypes` | `age` | `"int64"` | 1 (dtype, não valor) |
| `nested_missingness` | — | `null` | 5 |

Os 5 registros de `nested_missingness` não trazem coluna nem valor — a regra é
de linha e não aponta para um campo — mas as 5 linhas correspondentes estão
**inteiras** no parquet. O parquet é a exposição abrangente; o JSONL é a
pontual.

**Este é o único lugar do pipeline onde um valor de feature em claro é escrito
num arquivo de texto linha a linha.** A assimetria com os logs é total e é
deliberada: `logs/*.jsonl` tem `FORBIDDEN_FIELDS` e levanta exceção ao receber
`MonthlyIncome`; `data/quarantine/*/rejections.jsonl` grava `MonthlyIncome:
-1500.0` de propósito. Os dois arquivos têm o mesmo formato e o mesmo nome de
extensão, e um deles seria encaminhado ao Loki se alguém apontasse o coletor
para o diretório errado.

A contenção que existe hoje é o ponto de montagem: o Alloy monta **apenas**
`./logs`, somente leitura
([`docker-compose.monitoring.yml`](../docker-compose.monitoring.yml), serviço
`alloy`), e o `local.file_match` do
[`config.alloy`](../monitoring/alloy/config.alloy) só casa
`/var/log/credit-monitor/*.jsonl`. A quarentena não é coletada nem indexada.
É uma contenção por configuração, não por código — um volume a mais no compose
bastaria para desfazê-la.

### Por que a exceção se justifica mesmo assim

**A alternativa é pior para o titular.** Uma linha rejeitada é uma pessoa cuja
proposta não foi avaliada. Se o sistema descarta a linha e guarda só a contagem,
ninguém consegue responder à pergunta que importa — *foi a pessoa que mandou
dado errado, ou foi o nosso contrato que está errado?* Sem o valor observado, um
bug de parsing na origem que rejeita todas as rendas acima de um certo valor
fica invisível para sempre, e as pessoas atingidas não têm recurso porque não há
evidência de que algo aconteceu. **Guardar a evidência da rejeição é o que torna
a rejeição contestável.**

O que a exceção exige em troca é limite. Daí a **retenção de 90 dias**, a mais
curta de toda a tabela: é tempo de sobra para investigar um incidente de
ingestão — a janela real de investigação é de dias — e curto o suficiente para
que a exceção não vire um segundo dataset permanente crescendo à margem da
política. Quarentena é sala de espera, não arquivo.

> **[PRESCRITO]** — o expurgo de 90 dias da quarentena **não está
> implementado**. Hoje `data/quarantine/` só cresce, e `data/` inteiro está no
> `.gitignore` — o que impede o vazamento pelo git, e não é uma política de
> retenção.

### 3.2 Monitoramento precisa de história — e isso colide com a limitação de armazenamento

O Art. 6, V (qualidade) e o Art. 15 (término do tratamento) empurram para
apagar. A detecção de drift empurra para guardar: **PSI é uma comparação contra
uma referência**, e uma referência apagada não é substituível. Se a referência
da etapa 1 for tratada no mesmo relógio do dado operacional, todo limiar
calibrado do projeto — os 0,10 e 0,25 do PSI, o -0,0056 do gap, o 0,8372 do AUC,
todos derivados de sorteios do holdout — perde o chão sob os pés. Pior: o sistema
não acusa erro, ele passa a medir drift contra uma referência nova e reporta
tranquilamente que está tudo bem.

A reconciliação é reconhecer **o que o monitoramento realmente precisa**, e não
é a linha da pessoa:

| o monitoramento precisa de | é dado pessoal? |
|---|---|
| bordas dos bins de quantil por feature | não — são cortes numéricos |
| frequência esperada por bin | não — é uma contagem |
| quantis e momentos da distribuição de referência | não — agregado |
| média e desvio da distribuição nula do gap e do AUC | não — agregado sobre 500 sorteios |
| participação de cada feature no ganho | não — vem do modelo, não do dado |
| **a linha individual** | **sim — e não é necessária** |

Um PSI se calcula inteiro a partir de bordas e frequências esperadas. Guardar a
linha para recalcular o PSI é guardar o dado por conveniência de implementação,
não por necessidade da medida.

> **[PRESCRITO]** — a separação entre **estatística de referência** e **linha de
> referência** não está implementada. Hoje `reference.parquet` e
> `holdout.parquet` guardam linhas, e as estatísticas são recalculadas a partir
> delas a cada execução. Um sistema real congelaria o *fingerprint* estatístico
> num artefato versionado — bordas, frequências, quantis, as nulas dos limiares
> — e daria a ele retenção longa, deixando as **linhas** seguirem o prazo do
> dado operacional. É a mudança de desenho com melhor relação entre custo e
> privacidade em todo este documento: barata, e apaga a maior concentração de
> dado pessoal de longa duração do sistema.

---

## 4. Privacy by Design — os sete princípios

Cada princípio abaixo aponta para um arquivo. Onde não há arquivo, está escrito
que não há.

### 4.1 Proativo, não reativo — preventivo, não corretivo

**[IMPLEMENTADO].** O contrato de dados roda **antes** do modelo, não depois do
incidente. [`contracts/raw.py`](../src/credit_monitor/contracts/raw.py) e
[`contracts/severity.py`](../src/credit_monitor/contracts/severity.py) definem
13 regras com severidade; um lote que viola regra bloqueante **para o pipeline**
e não é pontuado. Dado ruim não chega ao modelo e não vira decisão sobre
ninguém — a prevenção é estrutural, e não um alerta que alguém precisa ler.

A prova de que isso é comportamento e não intenção: em
[`monitoring/run.py`](../src/credit_monitor/monitoring/run.py) o lote bloqueado
marca os estágios seguintes como `STAGE_SKIPPED`, **não publica** métrica de
drift nem de predição, e grava o veredito `blocked` no MLflow. Ausência, não
zero.

### 4.2 Privacidade como configuração padrão

**[IMPLEMENTADO].** Não há como um valor de feature entrar num log: é preciso
remover código para conseguir.
[`monitoring/logs.py`](../src/credit_monitor/monitoring/logs.py) define
`FORBIDDEN_FIELDS` com as dez colunas de feature, e `PipelineLogger.emit`
**levanta `ValueError`** ao receber qualquer uma delas. A identidade da linha
viaja como hash salgado (§5).

Fixado por três testes em
[`tests/test_monitoring.py`](../tests/test_monitoring.py):

- `test_log_records_never_contain_raw_feature_values`
- `test_attempting_to_log_a_feature_value_raises`
- `test_row_identity_travels_as_a_hash`

O padrão é o comportamento seguro; o inseguro exige esforço deliberado. É essa
assimetria que faz de um princípio um mecanismo.

### 4.3 Privacidade embarcada no projeto

**[IMPLEMENTADO], parcialmente.** O que é estrutural: a severidade vive no
contrato e não numa condicional espalhada pelo código; a quarentena é um
diretório com dono e formato definidos; os logs são JSON estruturado com rótulos
fixos. A separação entre `features.parquet`, `predictions.parquet` e
`labels.parquet` é de desenho e não de arrumação — o rótulo chega dois meses
depois, e forçar essa separação no disco impede o vazamento temporal de usar um
desfecho que ainda não existia.

**Não é [IMPLEMENTADO]:** não há cifragem em repouso, não há controle de acesso
por papel, não há segregação de ambiente. É um projeto local de avaliação.

### 4.4 Funcionalidade total — soma positiva, não soma zero

**[IMPLEMENTADO].** O caso concreto é o hash do §5: os logs perderam o valor
legível da linha e **não perderam capacidade de investigação**, porque o hash é
estável e uma consulta ao Loki continua seguindo uma linha por todo o pipeline.
A capacidade que importava foi preservada; o dado pessoal, não.

O contraexemplo honesto está na quarentena (§3.1), onde **não** houve soma
positiva: para manter a rejeição contestável foi preciso guardar o valor. O
princípio é uma meta, não uma descrição de tudo que o sistema faz — e dizer isso
vale mais do que declarar soma positiva em sete lugares.

### 4.5 Segurança de ponta a ponta

**[IMPLEMENTADO].** A cadeia que o repositório cobre: SHA-256 verificado no
download (`data/download.py`, com o digest esperado fixado em `constants.py`) →
contrato com severidade na ingestão → quarentena do que falha → curto-circuito
do lote bloqueado → logs sem dado pessoal → veredito registrado no MLflow. Cada
elo tem teste.

> **[PRESCRITO]** — o que **falta** para "ponta a ponta" merecer o nome: TLS
> entre os componentes (a stack fala HTTP puro em `localhost`), autenticação em
> Prometheus, Loki e Grafana (todos abertos, `auth_enabled: false`), cifragem em
> repouso dos parquets, gestão de segredos, e trilha de acesso — *quem leu o
> dado*, que é diferente de *o que o pipeline fez com ele*. Nada disso existe
> aqui.

### 4.6 Visibilidade e transparência

**[IMPLEMENTADO].** É a dimensão mais forte do projeto, e é a que a etapa 3
construiu:

- **MLflow como auditoria**: toda execução vira um run, com hash do dataset,
  versão do campeão e veredito. E só o que foi medido — um lote bloqueado não
  ganha métrica zerada, porque zero fabricado num rastro de auditoria é pior que
  ausência.
- **Três dashboards provisionadas como código** em `monitoring/grafana/`,
  geradas por `scripts/build_dashboards.py` e revisáveis no git.
- **Oito alertas como código** em `monitoring/rules/`, com testes de `promtool`
  escritos **antes** das regras (`make alerts-test` no CI).
- **[`docs/metrics.md`](metrics.md)** — catálogo de cada métrica, com limiar,
  origem do limiar, **falso alarme medido** e **atraso medido até o dano**.
- **Sete runbooks** em [`docs/runbooks/`](runbooks/), um por alerta, apontados
  pela anotação `runbook_url` da própria regra.
- **[`docs/findings.md`](findings.md)** — 13 achados, incluindo os que
  **refutaram** decisões anteriores do próprio projeto. O achado §13 registra
  que a correção de um limiar **piorou** o número reportado, e a correção está
  escrita onde a afirmação errada foi feita, não só onde ela foi desmentida.

O último item é o que dá lastro aos outros. Transparência que só publica o que
ficou bonito é publicidade.

### 4.7 Respeito pela privacidade do usuário — centrado no titular

**[IMPLEMENTADO], num sentido específico e defensável.** O trabalho de
calibração deste projeto é, antes de ser técnico, uma questão de justiça com a
pessoa avaliada.

O achado §5 mediu o seguinte: reponderar a classe para tratar o desbalanceamento
— o tratamento padrão, recomendado em todo lugar — faz o modelo **inflar as
probabilidades por um fator de ~14**, sem mexer no AUC (0,8584) nem no KS
(0,5677). O Brier vai a **0,1356**, mais que o dobro do **0,0623** de um modelo
que ignora todas as features e responde sempre a taxa-base. Um painel que
acompanhasse só ordenação nunca veria.

O que isso significa para uma pessoa real: ela é recusada por um número que **não
significa o que diz**. O sistema anuncia "risco de 50%" sobre alguém cujo risco é
~6%. Ela não tem como contestar, porque o número tem aparência de medida e
comportamento de opinião — e, pelo Art. 20 §1, é exatamente esse número que lhe
seria apresentado como "o critério". **Informar um critério que não significa o
que diz é cumprir a letra do artigo e violar o propósito dele.**

A correção está no repositório: calibração isotônica ajustada numa partição
separada (nem treino, nem validação), custando 0,0005 de AUC e melhorando o
Brier em **2,7x** — de 0,1356 para **0,0496**. O modelo com o alias `champion`
é o calibrado. É a diferença entre um score e um número.

> **[PRESCRITO]** — o respeito ao titular no sentido pleno do princípio inclui o
> que só um controlador real entrega: canal de atendimento ao titular, resposta
> aos Art. 18 (acesso, correção, portabilidade, eliminação) em prazo, aviso de
> privacidade em linguagem clara no ponto de coleta, e revisão humana efetiva.
> Nada disso existe aqui.

---

## 5. O hash salgado, e o que ele não é

### 5.1 O que está implementado

**[IMPLEMENTADO].** `hash_row_id` em
[`monitoring/logs.py`](../src/credit_monitor/monitoring/logs.py):

```python
digest = hashlib.sha256(f"{row_salt()}:{row_id}".encode()).hexdigest()
return digest[:16]
```

SHA-256 do id da linha prefixado pelo sal, truncado em 16 caracteres hex. O sal
vem de `row_salt()`, que lê a variável de ambiente `CREDIT_MONITOR_ROW_SALT` e,
na ausência dela, usa o padrão `DEFAULT_ROW_ID_SALT`, **que está commitado no
código-fonte**.

O truncamento em 16 hex é deliberado: 64 bits sobram para que colisão num lote
de dezenas de milhares de linhas não seja preocupação prática, e o hash cabe
numa linha de log sem estragar a leitura.

### 5.2 Isto é pseudonimização (Art. 13), não anonimização

A distinção é a que mais se erra em documento de privacidade, e ela é jurídica,
não estética.

**Dado anonimizado** (Art. 5, III) está **fora da LGPD** (Art. 12): é aquele que
perdeu a possibilidade de associação a um indivíduo, considerados meios técnicos
razoáveis e disponíveis.

**Dado pseudonimizado** (Art. 13, §4) **continua dentro da LGPD**: é aquele que
perde a possibilidade de associação *a não ser* pelo uso de informação mantida
separadamente. Ele continua sendo dado pessoal, com todas as obrigações que isso
traz.

O hash daqui é **pseudonimização**, sem margem para debate, e por três razões
independentes — qualquer uma bastaria:

1. **É determinístico.** Mesma entrada, mesmo sal, mesma saída, sempre. Essa é a
   propriedade que faz a consulta ao Loki funcionar, e é a mesma que permite
   fazer *linkage*: duas linhas com o mesmo hash são a mesma linha de dado,
   ainda que ninguém saiba qual.
2. **É invertível por enumeração quando o espaço de entrada é pequeno.** Este é
   o ponto que mais se exagera na direção errada. Hash **não** é cifragem e
   **não** protege domínio enumerável: com o sal em mãos e 150.000 ids
   possíveis, construir a tabela inteira leva **menos de um segundo** de CPU. O
   mesmo vale, num sistema real, para qualquer campo de baixa cardinalidade —
   hashear CPF, CEP ou data de nascimento e chamar o resultado de anonimizado é
   um erro caro, porque o espaço de busca cabe na memória de um notebook.
3. **O sal padrão está no repositório.** `DEFAULT_ROW_ID_SALT` é uma constante
   commitada, versionada e pública, junto com o dataset público. Quem clona o
   repositório tem os dois lados da tabela. **Aqui, hoje, o hash não é sequer
   pseudonimização eficaz contra alguém com o repositório — é higiene contra o
   acidente**, que é o risco real que ele foi feito para atacar: dado pessoal
   legível parado num índice de logs que ninguém lembra que existe.

O docstring do módulo já dizia isso antes deste documento, e continua dizendo:
o hash "não está protegendo contra um atacante determinado com o dataset — está
protegendo contra o acidente muito mais provável".

### 5.3 O que muda num sistema real

Uma coisa mudou **agora**, porque era barata e tornava o resto verdadeiro:

**[IMPLEMENTADO]** — o sal passou a ser lido de `CREDIT_MONITOR_ROW_SALT`. Uma
implantação real fornece o seu próprio sal por variável de ambiente, vindo do
cofre de segredos, **sem tocar no código**. O padrão commitado só vale quando a
variável está ausente, que é o caso deste repositório de avaliação. Fixado por
`test_the_salt_is_overridable_from_the_environment` em
[`tests/test_monitoring.py`](../tests/test_monitoring.py).

> **[PRESCRITO]** — o que continua faltando, e que a variável de ambiente
> sozinha não resolve:
>
> - **Custódia separada.** O Art. 13 §4 só concede o benefício da
>   pseudonimização quando a informação de reversão é "mantida em ambiente
>   controlado e seguro". Sal no cofre, com acesso distinto de quem lê os logs —
>   se a mesma pessoa tem o sal e o índice de logs, não houve separação nenhuma.
> - **Rotação.** Sal que nunca muda acumula exposição indefinidamente. Rotação
>   periódica, com a consequência aceita de que hashes antigos e novos deixam de
>   correlacionar — é o custo da rotação, e é o custo certo.
> - **HMAC em vez de hash simples**, ou um KDF com custo (Argon2, scrypt) quando
>   o domínio for enumerável. SHA-256 é rápido de propósito, e velocidade é
>   exatamente o que favorece quem enumera.
> - **Nunca chamar isso de anonimização** em nenhum documento, contrato ou
>   resposta a titular. É o erro que transforma um controle razoável numa
>   declaração falsa.

---

## 6. Limitações, reunidas

Para não obrigar ninguém a reler o documento inteiro atrás dos avisos.

1. **Dataset já anonimizado, sem titular identificável.** Por isso, e **só**
   por isso, o Art. 12 põe este tratamento fora da LGPD — não por ser público
   (Art. 7 §3) nem por ser acadêmico (Art. 4, II, *b*). Tudo em [PRESCRITO] é o
   regime que valeria no primeiro dia com dado real.
2. **A quarentena guarda a linha inteira e o valor em claro.** Medido: 12 linhas
   × 11 colunas em `rows.parquet`, e 8 de 13 registros do `rejections.jsonl`
   com `observed_value` preenchido (`age: 12`, `MonthlyIncome: -1500.0`). É o
   único ponto do pipeline que escreve valor de feature em arquivo de texto, e
   é deliberado (§3.1).
3. **Sem explicação por decisão individual.** SHAP ou equivalente não está
   implementado; o Art. 20 §1 pede exatamente isso.
4. **Sem revisão humana.** Não há processo, prazo, canal ou autoridade para
   reverter uma decisão automatizada.
5. **Retenção é documento, não mecanismo.** Só os 15 dias do Prometheus estão
   aplicados. Quarentena, lotes, predições, rótulos e Loki não têm expurgo.
6. **Loki sem retenção e sem autenticação.** `auth_enabled: false` e nenhum
   `retention_period`: logs indefinidos numa stack aberta.
7. **Sem cifragem em repouso, sem TLS, sem controle de acesso.** Stack local de
   avaliação, HTTP puro, sem autenticação em nenhum componente.
8. **Referência guarda linhas, não só estatísticas.** É a maior concentração de
   dado pessoal de longa duração do desenho, e §3.2 diz como resolver.
9. **Sal padrão é público.** Só a variável de ambiente move isso, e ela não está
   definida neste repositório — por escolha, já que aqui não há o que proteger.
10. **Viés não medido.** Etapa 5.

---

## 7. Onde conferir cada afirmação

| assunto | arquivo |
|---|---|
| ausência de identificador, as 11 colunas | [`reports/inspection.md`](../reports/inspection.md) §7 |
| contrato, severidade, regras | [`contracts/`](../src/credit_monitor/contracts/) |
| quarentena: código que grava | [`pipeline/ingest.py`](../src/credit_monitor/pipeline/ingest.py), `quarantine_rows` |
| quarentena: o que está gravado, medido | `data/quarantine/batch_20260920/` — 12×11 no parquet, 8 de 13 com `observed_value` (§3.1) |
| por que a quarentena não chega ao Loki | [`monitoring/alloy/config.alloy`](../monitoring/alloy/config.alloy) · montagem do serviço `alloy` no compose |
| logs sem dado pessoal, hash, sal | [`monitoring/logs.py`](../src/credit_monitor/monitoring/logs.py) |
| testes que fixam o comportamento dos logs | [`tests/test_monitoring.py`](../tests/test_monitoring.py) |
| rastro de auditoria e campeão | [`monitoring/run.py`](../src/credit_monitor/monitoring/run.py) · [`models/train.py`](../src/credit_monitor/models/train.py) |
| calibração e o custo de não calibrar | [`docs/findings.md`](findings.md) §5 |
| limiares, falso alarme e atraso | [`docs/metrics.md`](metrics.md) |
| retenção de métricas | `docker-compose.monitoring.yml:71` |
| retenção e autenticação de logs | [`monitoring/loki-config.yml`](../monitoring/loki-config.yml) |
