# Validação: como sabemos que funciona

[`docs/findings.md`](findings.md) responde "o que descobrimos" sobre o
problema — o dado, o modelo, as métricas. Este documento responde outra
pergunta: **como sabemos que o projeto funciona na máquina de outra pessoa.** É
a história das validações, na ordem em que aconteceram, com o que cada uma
achou e o que mudou por causa disso.

## 1. O método, e de onde ele veio

Na fase 1 do Tech Challenge, o avaliador não conseguiu montar o ambiente do
projeto. Custou 20 dos 90 pontos — não por um erro no modelo ou na análise, mas
porque o caminho entre o `git clone` e o primeiro resultado dependia de coisas
que só existiam na máquina de quem construiu.

Daí o método desta fase: **clone limpo, em dois sistemas operacionais,
seguindo só o README.** Diretório novo, nenhum `.venv`, `data/`, `mlruns/` ou
volume do Docker reaproveitado; o que o README não diz, não se faz. E a regra
que ele produziu, aplicada a cada tropeço:

> **Se foi preciso algo que o README não diz, o defeito está no README.**

Não "o avaliador deveria saber", não "é óbvio para quem conhece a ferramenta".
O README é a especificação do ambiente; uma lacuna nele é um defeito como
qualquer outro, com a diferença de que nenhum teste automatizado o encontra.

## 2. O ensaio no Mac: o procedimento documentado não funcionava

Três semanas depois do fim da construção, em 2026-10-12, o ensaio geral no
macOS — o mesmo procedimento que seria repetido no Windows. Clone novo do
`main`, `make install`, `make all`: passou, em 276 s contra os 271 s medidos, e
o `git status` saiu limpo, com os 20 arquivos versionados idênticos byte a
byte.

Pelos critérios que o projeto tinha até ali, estava validado. Faltava a etapa
3: `docker compose down -v` para começar com volumes vazios, `make stack-up`,
`make monitor-all`. E o `monitor-all` morreu depois de empurrar os 21 lotes
mensais:

```
nenhum lote sujo em data/dirty — rode `make validate-bad-batch`.
```

O monitor empurra o lote bloqueado da demonstração do portão como um dos seus
casos de borda, e esse lote só existia se alguém tivesse rodado
`make validate-bad-batch` antes — um alvo que fica fora do `make all` de
propósito, porque sai com código não-zero. O README nunca disse isso.

Por que ninguém tinha visto: o checkout de trabalho tinha
`data/dirty/batch_20260920.parquet`, gerado no dia 3 e nunca apagado. `data/` é
ignorado pelo git, então nenhum clone o traz e nenhum `git status` o mostra.
Toda execução do `monitor-all` desde a etapa 3 achou esse arquivo. A correção
(PR #13) fez o monitor gerar o lote quando falta e pôs a geração dentro do
`make all`; o README passou a mostrar os quatro passos na ordem real. Achado
[§16](findings.md).

O mesmo ensaio levantou, por leitura, a lista de riscos do Windows
([`windows-notes.md`](windows-notes.md)) e corrigiu de antemão os que eram
certos de quebrar — a data via `date` no `cmd`, as receitas POSIX, o download
do promtool. A intenção era que o Windows fosse confirmação, não descoberta.

## 3. O Windows: quatro defeitos e três diagnósticos errados

No Windows 11, pelo mesmo método, tudo o que é estrutural passou: `make all`
com os 251 testes, `make alerts-test` via Docker, os dois `validate-*` com os
códigos de saída certos, a stack inteira, as três dashboards, e os *lead times*
-1 e -2 idênticos aos do macOS. Quatro defeitos, nenhum bloqueante — e cada um
deles teria custado credibilidade diante do avaliador.

**A dashboard de feature abria vazia.** O que o avaliador veria: "No data" nos
cinco painéis da `cm-feature` até escolher uma feature à mão — uma dashboard que
parece quebrada. O primeiro diagnóstico foi "a variável não tem valor inicial".
**Estava errado:** o JSON gerado tinha o padrão desde o primeiro dia. O
problema era de onde vinha a lista: uma consulta
`label_values(drift_psi, feature)` ao Prometheus, rodada uma vez ao carregar a
dashboard. Aberta antes de o `monitor-all` ter empurrado qualquer coisa, a
consulta volta vazia, o Grafana descarta o padrão por não constar da lista, e
não pergunta de novo. No Mac não reproduziu, porque no Mac o Prometheus sempre
já tinha dado. A correção trocou a consulta por uma lista fixa das features do
modelo, com padrão na de maior participação no ganho. A causa é inferida — não
foi reproduzida — e fica a confirmar no próximo teste no Windows.

**Oito arquivos versionados apareciam modificados.** O que o avaliador veria:
`git status` sujo logo depois de um `make all` que o README promete limpo — os
três JSON das dashboards e cinco relatórios. A causa: o Python em modo texto
grava `\r\n` no Windows, e nenhuma das escritas fixava `newline=`. A correção
cobriu **todas** as escritas de texto do projeto, não só as oito que
apareceram — 17 com `write_text`, a ingestão, o HTML do Evidently (que abria o
arquivo em modo texto por conta própria) e o handler dos logs. Um teste lê o
código e falha em qualquer escrita nova sem `newline=`.

Aqui houve um terceiro diagnóstico errado, este feito antes do teste: a lista
de riscos previa que o `git status` *esconderia* o CRLF, porque o
`.gitattributes` normaliza o fim de linha. Não escondeu — marcou os arquivos
como modificados, com `git diff` vazio. A previsão foi corrigida no README.

**Caminhos com contrabarra dentro dos relatórios.** O que o avaliador veria:
`reports/inspection.md` dizendo `data\raw\gmsc.parquet`. A causa: `str(path)`
usa o separador do sistema, e o texto de um relatório não pode depender do
sistema que o escreveu. A correção: toda escrita de caminho em relatório, JSON,
log ou console passa por uma função que o devolve relativo ao repositório e
sempre com `/`.

**Acentos quebrados no console.** O que o avaliador veria: `make stack-up`
imprimindo `an├┤nimo`. O primeiro diagnóstico, feito na lista de riscos,
apontava para o Python — e o Python estava certo, porque escreve no console do
Windows em UTF-16. **Quem quebrava era o próprio `make`**: o `$(info)` escreve
os bytes UTF-8 do Makefile direto num console em cp850, onde o `ô` (C3 B4) vira
`├┤`. A correção põe o console em UTF-8 (`chcp 65001`) quando o `make` começa,
no Windows. Também a confirmar.

## 4. O que não tem conserto: a cadeia de amplificação

O XGBoost não é bit a bit idêntico entre macOS ARM e Windows x64. Mesma
semente, mesmo dado, mesmo `uv.lock`: AUC 0,8561 contra 0,8553. Fixar
`OMP_NUM_THREADS=1` não fez os dois convergirem — deu um terceiro valor,
0,8557 —, o que descarta a ordem das reduções paralelas e aponta para o build
da biblioteca em cada plataforma. A regressão logística sai idêntica nas duas.

Isso não se conserta num projeto; documenta-se. E o interessante é o que
acontece com essa diferença ao longo da cadeia, porque ela **cresce a cada
elo**:

1. **Plataforma → scores.** Diferença no último dígito das probabilidades.
2. **Scores → AUC.** 0,0008, cerca de 0,1% — uma ordem de grandeza abaixo do
   piso de ruído entre sementes (±0,007) que o projeto já tinha medido.
3. **Scores → KS máximo → limiar de operação.** 0,0804 no macOS contra 0,0894
   no Windows: **11%**. O limiar é escolhido no ponto de KS máximo, e o KS é
   achatado em volta do máximo — um deslocamento mínimo dos scores move bastante
   o ponto escolhido. É o elo onde a diferença salta duas ordens de grandeza.
4. **Limiar → taxas de aprovação por faixa etária.** Todo cliente perto do
   corte muda de lado; as taxas de aprovação de cada faixa mudam com ele.
5. **Taxas por faixa → métricas de justiça.** Paridade demográfica e chances
   equalizadas são diferenças entre essas taxas, e herdam a variação.

E em cada elo **a conclusão se mantém**. O XGBoost vence a regressão logística
por +0,0071 no macOS e +0,0072 no Windows, 5 de 5 sementes nas duas. Os *lead
times* são -1 e -2 nas duas. No capítulo de viés, a faixa 66+ segue a mais
prejudicada no mês 6, e a calibração por grupo piora enquanto paridade e chances
equalizadas melhoram — nas duas plataformas, com o limiar de cada uma.

O que isso mudou foi a promessa: o README dizia que todo relatório versionado se
reproduz byte a byte; agora diz que isso vale **na mesma plataforma**, e que
**entre plataformas reproduzem as conclusões**. Os arquivos que não passam pelo
modelo — `reports/inspection.md` e as três dashboards — continuam idênticos em
qualquer sistema, e são eles que denunciam um defeito de verdade. Achado
[§17](findings.md).

Os valores por faixa do Windows não estão versionados no repositório — só os
do macOS, em `reports/fairness/bands.json`. A afirmação sobre os elos 4 e 5 é
qualitativa por isso.

## 5. A lição transversal: estado sobrando esconde defeito

É o ponto deste documento. Aconteceu três vezes, sempre igual:

1. **Séries velhas no Pushgateway esconderam seis painéis quebrados.** Os
   painéis de AUC e gap da visão geral apontavam para o mês 6, que nunca tem
   rótulo. Não pareciam quebrados porque o Pushgateway servia séries de
   execuções antigas — a admin API estava desligada e o wipe falhava em
   silêncio. Foi consertar o wipe que expôs os painéis.
2. **Um lote sujo esquecido em disco escondeu uma dependência não declarada.**
   O `monitor-all` precisava de um arquivo que só outro alvo produzia, e um
   arquivo de três semanas antes satisfazia a dependência em toda execução.
3. **Uma variável de dashboard só funcionava porque o Prometheus já tinha
   dado.** Em toda máquina onde a stack já tinha rodado, a consulta que
   alimentava a lista de features nunca voltava vazia.

Nos três casos, o ambiente configurado **mentiu**: respondeu "funciona" por
causa de algo que o código não produzia. E nos três, quem disse a verdade foi
um ambiente sem nada sobrando — um clone limpo, volumes vazios, uma máquina
nova. Testes de unidade não veem esse tipo de defeito, porque cada teste monta o
próprio estado; o CI não vê, porque roda testes, não a cadeia; e a máquina de
quem desenvolve é exatamente onde o estado sobra.

É por isso que **simular o avaliador é a checagem de maior retorno do
projeto.** Custa uma tarde e um diretório novo, e achou o que nenhuma outra
achou.

## 6. O que não foi testado

Dito sem rodeio, para que ninguém leia a ausência como aprovação:

- **Linux.** O CI roda em Ubuntu, mas só `make lint`, `make test` e
  `make alerts-test` — não o `make all` nem a stack.
- **Uma máquina sem Docker.** O `make all` não precisa de Docker e foi escrito
  para isso, mas as duas máquinas do teste tinham Docker instalado. Não está
  provado que nada da cadeia o toca.
- **Redes restritas.** O dataset vem do OpenML e as imagens do Docker Hub; um
  proxy corporativo pode bloquear os dois, e o comportamento nesse caso não foi
  exercitado.
- Na lista do Windows, também não exercitados: o atraso do Alloy lendo logs por
  bind mount (W10) e caminhos longos (W16); e não reportados: W8, W9, W11
  ([`windows-notes.md`](windows-notes.md)).
- As quatro correções feitas depois do teste no Windows — a variável da
  dashboard, o `newline=`, os caminhos com `/` e o `chcp` — foram verificadas
  no macOS, mas ainda não rodaram numa máquina Windows.
