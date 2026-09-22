# Model Card — campeão de *credit scoring*

**Modelo:** `credit-default-champion`, alias `champion`, versão **1**
**Tipo:** XGBoost calibrado por isotônica · classificação binária
**Registro:** MLflow Model Registry (SQLite local) · run `fa440fd1e9be4221b990fc6722c1b321`
**Data:** etapa 1, 2026-09-20 · card escrito na etapa 4
**Dataset:** *Give-Me-Some-Credit* (OpenML id 45577), público e já anonimizado

---

## 1. Uso pretendido

**Para que serve.** Estimar a probabilidade de uma pessoa sofrer inadimplência
grave (90+ dias de atraso) nos **dois anos** seguintes, a partir de 11 features
de crédito. A saída é uma probabilidade calibrada, não uma classe.

**Para que este modelo existe neste projeto.** Ele é o **sujeito de um sistema
de monitoramento**, não o produto. Todo o trabalho das etapas 2 a 4 — simulação
de drift, detecção estatística, observabilidade, governança, viés — precisa de
um modelo realista para vigiar. As decisões de modelagem foram tomadas com esse
papel em mente, e pelo menos uma delas (o teto de utilização em 10,0, achado §7)
foi decidida **contra** a métrica de modelo e a favor da capacidade de detecção.

**Usuários pretendidos.** Quem avalia este trabalho, e quem quiser reproduzir a
camada de monitoramento sobre outro modelo.

### Fora de escopo

- **Não é apto a decidir crédito para pessoas reais.** Nada aqui foi validado
  contra uma carteira real, uma política de crédito ou um custo de perda.
- **Não decide sozinho.** Não há revisão humana, canal de contestação nem
  explicação por decisão individual — ver §6 e `docs/governanca.md` §2.2.
- **Não vale para a população de maior risco do dataset.** As 269 linhas com
  códigos-sentinela são removidas antes do treino. Ver §6.
- **Não foi avaliado quanto a viés racial ou de gênero**, porque o dataset não
  tem esses atributos. Ver `docs/vies.md` §1.

## 2. Dados

| partição | linhas | papel |
|---|---|---|
| `train` | 61.891 | ajusta o modelo reponderado |
| `calibration` | 20.630 | ajusta o mapa isotônico **sobre** o modelo já treinado |
| `validation` | 20.631 | de onde saem as métricas reportadas e o limiar de operação |
| `holdout` | 44.208 | intocado na etapa 1; base da simulação e da análise de viés |

Bruto: 150.000 linhas, **6,68%** de positivos, verificado por SHA-256 a cada
download (`constants.py`).

**Três partições, não duas, e o motivo é específico:** ajustar o calibrador nas
mesmas linhas em que ele é medido reportaria a memória do calibrador, não o
comportamento dele.

### Features (11)

Dez colunas do dataset mais `income_missing`, uma *flag* derivada — a ausência
de renda é informativa e o modelo a usa explicitamente em vez de recebê-la
disfarçada de valor imputado.

Concentração do ganho do XGBoost: as quatro primeiras features — os três
contadores de atraso e a utilização do rotativo — respondem por **82,07%**.
`MonthlyIncome` responde por **2,15%**, nona de onze. Esse número é pequeno e
tem consequência: ele é a razão pela qual o mecanismo de inflação nominal é
quase inerte neste modelo (`docs/causalidade.md` §3).

### Pré-processamento

- teto de **10,0** na utilização do rotativo e de **2,0** no `DebtRatio`
- imputação da renda ausente com a mediana do treino, **com** `income_missing`
- remoção das 269 linhas com sentinelas 96/98 nos contadores de atraso
- contrato Pandera com severidade: 13 regras, BLOCKER para e WARNING registra

## 3. Treinamento

Dois candidatos, mesma partição, mesma semente (20260920): regressão logística e
XGBoost. Seleção por AUC-ROC na validação.

**Desbalanceamento tratado por reponderação** — `scale_pos_weight = 13,975`
no XGBoost, `class_weight="balanced"` na logística — **e depois desfeito por
calibração isotônica**. Os dois passos são deliberados e a §5 explica por quê.

## 4. Métricas

### O piso de ruído vem primeiro — nas duas métricas

Um delta reportado sem o ruído do procedimento que o produziu é um delta sem
interpretação. Cinco sementes, mesmo dado, variando só a partição interna
(`make seed-noise`, `reports/seed_noise.json`):

| campeão (xgboost, calibrado) | média | **desvio** | amplitude |
|---|---|---|---|
| AUC-ROC | 0,8616 | **0,0063** | 0,0145 |
| KS | 0,5720 | **0,0133** | 0,0332 |

**O KS é duas vezes mais ruidoso que o AUC em nível absoluto.** Isso responde à
pergunta que a etapa 1 deixou aberta, e responde contra a intuição de quem
esperava o contrário.

### E o piso certo para comparar dois modelos não é esse

O desvio acima é do **nível** de cada modelo. A pergunta "o campeão é melhor" é
sobre a **diferença**, e os dois modelos veem **a mesma partição** em cada
semente — o ruído da partição é comum aos dois e cancela na subtração:

| delta (xgboost menos logística) | média | **desvio** | vitórias |
|---|---|---|---|
| AUC-ROC | **+0,0071** | **0,0015** | **5 de 5** |
| KS | **+0,0190** | **0,0027** | **5 de 5** |

Valores do delta de AUC, semente a semente: +0,0070 +0,0078 +0,0084 +0,0076
+0,0046. Nunca negativo, nunca perto de zero.

> **Correção.** A primeira versão deste card dizia que a vantagem do campeão era
> **limítrofe** — "+0,0070 contra um piso de ruído de ±0,0070, uma sexta semente
> poderia inverter a ordem". Isso estava **errado**, e errado pelo mesmo motivo
> que o achado §15: eu comparei uma **diferença pareada** contra um **desvio não
> pareado**. A partição varia, mas varia igual para os dois modelos. O piso
> correto é 0,0015, não 0,0063, e a margem de AUC é **4,7 desvios**, não um.

**A vantagem do campeão não é limítrofe.** É +0,0071 de AUC com desvio de
0,0015 e 5 vitórias em 5, e +0,0190 de KS com desvio de 0,0027 e 5 em 5.

### Os dois candidatos (validação, calibrados)

| modelo | AUC-ROC | KS | Brier |
|---|---|---|---|
| **xgboost (campeão)** | **0,8561** | **0,5636** | **0,0499** |
| logistic_regression | 0,8491 | 0,5451 | 0,0504 |
| **margem do campeão** | **+0,0070** | **+0,0185** | +0,0005 |

**Leitura da margem, contra o piso pareado:**

- **AUC: +0,0070 nesta semente, +0,0071 na média de cinco, desvio 0,0015.**
  Cerca de **4,7 desvios**, e o campeão vence em **5 de 5**. Sólido.
- **KS: +0,0185 nesta semente, +0,0190 na média, desvio 0,0027.** Cerca de
  **7 desvios**, **5 de 5**. Ainda mais sólido — a separação é onde a diferença
  entre as duas famílias realmente aparece.
- **Brier: +0,0005. Empate.** Depois de calibrados, os dois entregam
  probabilidades igualmente boas, e é isso que importa para o titular.

A conclusão que se sustenta: **o XGBoost ordena melhor de forma consistente, e
empata na qualidade da probabilidade.** Um projeto que precisasse de um modelo
mais simples e auditável perderia ordenação e **não** perderia calibração
trocando pela logística — o que é uma troca defensável e está dita aqui em vez
de escondida atrás de um "o XGBoost venceu".

### Justiça por faixa etária (holdout, limiar 0,0804)

| critério | medido |
|---|---|
| paridade demográfica | **0,3616** |
| chances equalizadas | **0,3259** |
| **calibração por grupo** *(o critério priorizado)* | **0,0159** |
| diferença de taxa-base entre faixas | 0,0827 |

Os três não podem valer ao mesmo tempo com taxas-base diferentes. O sistema
prioriza **calibração por grupo**. Análise completa e a justificativa em
[`docs/vies.md`](vies.md) §6.

## 5. Calibração — e por que ela é um argumento de justiça

Esta é a decisão mais consequente do modelo e a menos óbvia.

**O que a reponderação faz.** Dizer ao estimador que positivos são **13,97
vezes** mais frequentes do que são. Ele aprende a **ordenar** bem e a reportar
probabilidades infladas por aproximadamente esse fator.

**Por que quase todo relatório não vê.** As métricas reagem de formas
diferentes:

| métrica | sensível a transformação monotônica? | efeito da reponderação |
|---|---|---|
| AUC-ROC | não | 0,8567 → 0,8561 (nenhum) |
| KS | não | 0,5668 → 0,5636 (nenhum) |
| **Brier** | **sim** | **0,1374 → 0,0499 (2,8×)** |

AUC e KS só enxergam a **ordem** dos scores, e reponderar não muda a ordem. Um
painel com essas duas métricas nunca vê o problema.

**A referência que torna o número chocante.** Um modelo que ignora todas as
features e responde sempre a taxa-base de 6,68% tem Brier `p(1-p)` = **0,0623**.
O modelo reponderado, com AUC de 0,86, tem Brier **0,1374** — mais que o dobro
do pior modelo concebível.

**O número medido que mostra o tamanho da distorção:** o ponto de corte do KS
fica em **0,4787** no modelo reponderado e em **0,0804** no calibrado. É a
**mesma decisão** sobre a mesma pessoa, anunciada como *48% de risco* num caso e
*8%* no outro.

### Por que isto é justiça e não métrica

Pelo **Art. 20 §1** da LGPD, o titular tem direito a informações claras sobre
**os critérios** da decisão automatizada. O critério, aqui, é esse número.

Uma pessoa recusada pelo modelo reponderado recebe "seu risco estimado é 48%"
quando o risco real é ~8%. Ela não tem como contestar: o número tem aparência de
medida e comportamento de opinião. **Informar um critério que não significa o
que diz é cumprir a letra do artigo e violar o propósito dele** — e é um dano
que recai sobre quem já foi recusado, não sobre quem opera o sistema.

**Correção adotada:** isotônica ajustada numa partição separada. Custa **0,0005
de AUC** e leva o Brier de 0,1374 a **0,0499**. O modelo com o alias `champion`
é o calibrado.

## 6. Limitações

1. **A vantagem do campeão é sólida, e a versão anterior desta limitação estava
   errada.** Os dois pisos de ruído foram medidos (cinco sementes): em **nível**,
   desvio de **0,0063** no AUC e **0,0133** no KS — o KS é o dobro de ruidoso. Mas
   a comparação entre modelos é **pareada** (mesma partição para os dois em cada
   semente), e o desvio do **delta** é **0,0015** no AUC e **0,0027** no KS, com
   o campeão vencendo em **5 de 5** nas duas métricas. A margem de AUC é 4,7
   desvios, não um. O que continua verdadeiro é a ressalva de escopo: as cinco
   sementes variam a partição **interna** do `reference`; a divisão
   reference/holdout é fixa, então este piso **não** cobre o ruído dessa divisão.

2. **As 269 linhas de sentinela estão fora do treino, e são a população que mais
   precisa de decisão.** Os códigos 96/98 nos contadores de atraso marcam 269
   linhas (0,18%) que inadimplem a **54,65%** — contra 6,68% da base. Elas são
   removidas no pré-processamento e o contrato as bloqueia em produção
   (`delinquency_sentinels`, BLOCKER). O comportamento é correto e a
   consequência precisa ser dita: **o sistema recusa decidir sobre o grupo de
   maior risco medido do dataset.** É contenção de escopo, **não** um problema
   resolvido. Um sistema real precisaria de uma política explícita para essas
   linhas — encaminhamento manual, modelo próprio, ou recusa declarada — e este
   não tem nenhuma.

3. **Sem explicação por decisão individual.** O modelo explica a si mesmo (ganho
   por feature); não explica **a decisão sobre uma pessoa**. SHAP ou equivalente
   não está implementado, e o Art. 20 §1 pede exatamente a segunda coisa.

4. **Sem revisão humana.** Não há processo, prazo, canal nem autoridade para
   reverter uma decisão automatizada.

5. **Viés medido em um eixo só.** Idade. O dataset não tem raça, sexo ou estado
   civil, e nada foi simulado para preencher a falta (`docs/vies.md` §1). Nada
   aqui autoriza concluir que o modelo não tem viés nesses eixos.

6. **A disparidade de aprovação por idade é grande e não foi mitigada.** 54% de
   aprovação na faixa 18-25 contra 95% na 66+ — **36 pontos**. Ela acompanha uma
   taxa-base que difere quatro vezes entre as faixas, o que é uma defesa e não
   uma absolvição: **a taxa-base vem de decisões de crédito passadas**, e se a
   concessão histórica foi enviesada, a calibração reproduz o viés com aparência
   de acerto. Este dataset não permite separar as duas coisas.

7. **O limiar de operação é um substituto.** 0,0804 sai do KS, não de uma razão
   de custos entre aprovar um inadimplente e recusar um bom cliente. Essa razão
   é decisão de negócio e não existe aqui.

8. **Duas features imputadas sem avaliação de impacto.** A renda ausente
   (19,82% das linhas) recebe a mediana do treino. A *flag* preserva a
   informação de ausência; o efeito do valor imputado sobre o score individual
   não foi medido.

9. **Degradação medida em mundo simulado.** O perfil da §7 vem da simulação da
   etapa 2, cujos mecanismos e magnitudes foram escolhidos por nós
   (`docs/causalidade.md` §5).

10. **Modelo de 2026-09-20, nunca retreinado.** Não há histórico de versões
    porque só existe a versão 1.

## 7. Considerações éticas

**O perfil de degradação, medido.** Seis meses de simulação com os três
mecanismos ligados:

| | mês 0 | mês 6 |
|---|---|---|
| AUC-ROC | 0,8601 | **0,7779** |
| gap de calibração | -0,0027 | **-0,0396** |
| inadimplência observada | 7,08% | **24,46%** |
| previsto médio | 6,81% | **20,50%** |

No mês 6 o modelo prevê 20,5% onde o observado é 24,5%. **Subestima o risco de
quase quatro pontos**, e a pessoa avaliada recebe um número que erra para menos
— o que, num sistema de concessão, produz aprovações que não deveriam acontecer
e uma carteira pior do que o painel indica.

**E o monitoramento não avisa antes.** O *lead time* assinado é **-1 mês** no
cenário completo e **-2 meses** no de estresse puro. Não há cenário positivo: no
melhor caso o alarme de drift chega **um mês depois** do dano; no pior, **não
chega nunca** e o único sinal é o desfecho, dois meses atrasado (`findings.md`
§13).

**Quem é mais prejudicado pela degradação:** a faixa **66+**, com gap de
**-0,0730** no mês 6 (IC 95% [-0,1112, -0,0403]) — quase três vezes o da faixa
18-25. Contraria a expectativa, porque o mecanismo de composição empurra a
carteira para os jovens; quem sai pior é quem ficou (`docs/vies.md` §7).

**E o painel aponta para o mecanismo errado.** A ablação mostra que o dano de
calibração por faixa vem do **estresse**, que é invisível ao PSI (máximo
0,0082), enquanto a **composição** — que domina o painel com PSI 0,9327 e cinco
features vermelhas — deixa a calibração intacta em cinco das seis faixas. Quem
olhar o painel de drift verá, em cores, o mecanismo que **não** está causando o
dano (`docs/vies.md` §8).

**Privacidade.** Dataset público e já anonimizado, sem titular identificável;
pelo Art. 12 o tratamento fica fora da LGPD. O regime que se aplicaria a
candidatos reais está em [`docs/governanca.md`](governanca.md), em duas camadas.

## 8. Manutenção

### Quando retreinar

| gatilho | sinal | decisão |
|---|---|---|
| **gap de calibração rompido sem drift de feature** | `CalibrationGapBreach` sem `FeatureDrift` | **Retreinar.** É mudança de conceito, e nenhum ajuste de limiar recupera um score que parou de significar o que diz |
| **drift de feature com calibração intacta** | `FeatureDrift` / `FeatureDriftCritical` sem `CalibrationGapBreach` | **Não retreinar por isto.** A carteira mudou e o modelo continua certo sobre ela. Verificar se a mudança foi intencional |
| **os dois juntos** | ambos | Retreinar, tratando como conceito, e investigar a composição em paralelo |
| **AUC abaixo do nulo** | `AUCDrop` (≤ 0,8372) | Revisar, não retreinar automaticamente: é ordenação, e ordenação cai por composição sem que o modelo esteja errado |
| **contrato bloqueando** | `ContractBlocked` | **Não é o modelo.** É a origem do dado |
| **cadência** | — | Revisão trimestral mesmo sem alerta, porque o pior caso (`stress_only`) **não dispara alerta nenhum** de drift |

A tabela é a mesma do diagnóstico de [`docs/causalidade.md`](causalidade.md) §4,
lida como política em vez de como leitura.

### O que trava a decisão: o rótulo

**Nenhuma linha dessa tabela que dependa de calibração ou de AUC pode ser
decidida antes de `label_lag_months = 2`.** Por dois meses a única evidência
disponível é drift de feature, que é exatamente o sinal que **não** vê mudança
de conceito. A métrica `labels_pending_batches` existe para que essa espera
apareça na tela em vez de ser esquecida.

A consequência de engenharia, registrada no achado §13: **encurtar o atraso de
rótulo é a única alavanca que reduz a janela cega.** Melhorar o detector de
drift não move esse número. Rótulo parcial em um mês em vez de dois o corta pela
metade.

### Quem decide

**[PRESCRITO]** — não há papéis definidos neste projeto. Um sistema real
precisaria nomear quem pode aprovar um retreino, quem pode aprovar um *rollback*
para a versão anterior, e quem responde por uma decisão contestada pelo titular
sob o Art. 20. Nada disso existe aqui, e nomear papéis fictícios seria inventar
governança.

### Como reverter

O alias `champion` aponta para uma versão registrada. Mover o alias de volta é a
reversão, e é atômica. Como só existe a **versão 1**, não há para onde reverter
hoje — o que é uma limitação real do estado atual, não uma propriedade do
desenho.

### Onde olhar

| pergunta | lugar |
|---|---|
| o que está acontecendo agora | Grafana `cm-overview` · `cm-drilldown` · `cm-feature` |
| o que foi decidido, sobre qual dado | MLflow: um run por execução de monitoramento |
| o que fazer quando um alerta dispara | [`docs/runbooks/`](runbooks/) — um por alerta |
| limiar, falso alarme e atraso de cada alerta | [`docs/metrics.md`](metrics.md) §4.3 |
