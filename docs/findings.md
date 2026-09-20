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
