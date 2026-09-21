# Roteiro de trabalho — vídeo STAR

Material de trabalho, não registro de achados. `docs/findings.md` guarda
**tudo**; este arquivo **escolhe**. Cinco minutos comportam cerca de quatro
ideias, e a decisão difícil é o que fica de fora — por isso a seção
"Descartados" existe e é tão importante quanto as outras.

Atualizado ao fim de cada dia (ver regra no README).

---

## 1. A história em uma frase

> **Um monitor de drift pode estar verde enquanto o modelo erra, e vermelho
> enquanto o modelo acerta — então vigiar as features, sozinho, não é monitorar
> o modelo.**

A frase abre e fecha o vídeo. As duas metades são demonstráveis com o **mesmo
dataset**, o que é o que a transforma de provocação em resultado:

- *verde enquanto erra*: `stress_only`, mês 6 — PSI máximo **0,0082**, gap de
  calibração **-0,0337**
- *vermelho enquanto acerta*: `composition_only`, mês 6 — PSI **0,9327**, cinco
  features vermelhas, gap **-0,0028**

Variante mais curta, se o tempo apertar: *"Drift não é degradação, e degradação
não exige drift."* Perde a consequência de engenharia; usar só na tela, com a
frase longa falada por cima.

---

## 2. Achados-herói (máximo 4)

Cada um: o número que prova, o artefato na tela, e a versão falada de ~15s.

### A. O 2x2 — **a peça central**

> achado #9 · `docs/findings.md`

| | |
|---|---|
| **Número** | `stress_only` mês 6: PSI máx **0,0082** (verde) com gap **-0,0337**; `composition_only`: PSI **0,9327** (vermelho) com gap **-0,0028** |
| **Na tela** | Os dois HTML lado a lado: `reports/evidently/stress_only_scoring_month_06.html` e `reports/evidently/stress_only_label_month_06.html` |
| **Fallback** | `reports/simulation/summary.md` §5, a tabela do 2x2 |

**Falado (~15s):**
> "Este é o mesmo lote, o mesmo mês, nos dois relatórios. À esquerda, o que o
> monitoramento vê hoje: todas as features verdes, PSI máximo de zero vírgula
> zero zero oito. À direita, o mesmo lote quando os rótulos chegam, meses
> depois: o modelo perdeu quatro pontos de AUC e passou a errar o nível de risco
> em três pontos percentuais. Vigiar feature não pega isso. Nunca vai pegar."

**Por que é a central:** é a única que muda o que a pessoa faz na segunda-feira.
As outras três são boas histórias; esta é uma recomendação.

### B. O Pearson esconde a cauda — **uma história, três vezes**

> achados #4, #6 e #8 contados como **um só**

| | |
|---|---|
| **Número** | 0,9878 → **0,2729** ao remover 269 linhas (0,18%); -0,0018 → **+0,2816** ao remover 241 linhas (0,16%) |
| **Na tela** | `docs/findings.md` §6, a tabela das três colunas que falharam o mesmo cheque |
| **Comando** | `make recheck-correlation` (imprime as duas matrizes) |

**Falado (~15s):**
> "O relatório de inspeção dizia que os três contadores de atraso eram cópias um
> do outro, correlação zero vírgula noventa e nove. Eram duzentas e sessenta e
> nove linhas — zero vírgula um oito por cento — fabricando o número. Sem elas,
> zero vírgula vinte e sete. Cometi o mesmo erro mais duas vezes, no teto de
> utilização e na explicação da renda. Neste dataset, um Pearson perto de zero é
> evidência de que a coluna tem cauda."

**Nota de edição:** contar como **progressão**, não como três achados. O gancho é
"errei de novo, depois de escrever a lição". A terceira ocorrência (#8) é a que
dá o arco: a conclusão estava certa, a evidência estava contaminada, e a correção
veio de ganho de 2,15% em vez do coeficiente.

### C. Ferramentas falham em silêncio — **uma história, dois exemplos**

> achados #1 e #10

| | |
|---|---|
| **Número** | Pandera: **9 das 13** regras perdiam o nome, e **os 3 alertas** estavam entre elas · Evidently: `DebtRatio` 0,0244 🟢 contra 0,1083 🟡 |
| **Na tela** | `reports/evidently/summary.md` §1, a tabela de cross-check com o ❌ |

**Falado (~15s):**
> "Duas ferramentas, o mesmo tipo de falha. O Pandera descartava o nome de
> regras herdadas — nove das treze, e os três alertas do projeto estavam entre
> elas; todo alerta teria virado bloqueante sem nenhuma mensagem de erro. O
> Evidently discorda do nosso PSI o suficiente para trocar um veredito, porque
> usa bins de largura igual cujo resultado depende de um parâmetro que ninguém
> escolheu. Ferramenta boa, comportamento silencioso."

**Nota de edição:** o par é mais forte que qualquer um sozinho, porque mostra que
não é azar com uma biblioteca. Se faltar tempo, cortar o Pandera e manter o
Evidently — é a ferramenta que a rubrica exige.

### D. O lote de controle que pegou o bug

> achado #7

| | |
|---|---|
| **Número** | AUC do mês 0: **0,5857** com o bug, **0,8601** correto |
| **Na tela** | `reports/simulation/summary.md` §3, a linha do mês 0 |

**Falado (~15s):**
> "O `pyfunc` do MLflow devolve a classe prevista, não a probabilidade. Com isso
> o AUC lia zero vírgula cinquenta e nove. Numa simulação de drift, um modelo
> degradado é exatamente o que se espera ver — nenhum alerta dispara. O que
> pegou foi o lote do mês zero, que é controle e tinha de reproduzir um número
> que eu já conhecia. Lote de controle não é formalidade."

### E. `[RESERVADO — dia 8, teste A/A]`

O resultado do A/A pode **deslocar** um dos quatro acima. Candidato natural a
sair: **D**, que é o mais técnico e o menos acionável. Se o A/A mostrar taxa de
falso alarme alta com p-valores, ele se encaixa direto ao lado de **C** como
terceiro exemplo de "a ferramenta responde com confiança a uma pergunta que você
não fez".

---

## 3. Tomadas de demonstração

Todas precisam **funcionar no momento da gravação**. Rodar a lista inteira antes.

### D1 — O portão discrimina

```bash
make validate-bad-batch     # BLOCKED, sai 1
make validate-clean-batch   # ACCEPTED_WITH_WARNINGS, sai 0
```

**Na tela:** os dois vereditos em terminais lado a lado. O `>>> ingestão saiu com
código 1 — pipeline interrompido, como esperado` precisa aparecer.
**Por que:** mostra que o portão reprova por motivo, não por reflexo. Dez
segundos, alto impacto.
**Pré-requisito:** `make prepare` e `make train` já rodados.

### D2 — A rampa de PSI

**Caminho:** `reports/simulation/summary.md` §2 (ou a saída de `make simulate`).
**Na tela:** a tabela com o degradê 🟢🟡🔴 descendo pelos meses. Pausar no mês 3,
onde três features entram no amarelo.
**Por que:** prova que o cenário é uma rampa e não um degrau — a calibração foi
feita contra quatro alvos declarados antes.

### D3 — O gap de calibração mês a mês

**Caminho:** `reports/simulation/summary.md` §3.
**Na tela:** a coluna do gap, de **-0,0027** a **-0,0396**, ao lado da
inadimplência observada indo de **7,08%** a **24,46%**.
**Por que:** é a degradação silenciosa em uma coluna. O AUC cai 9,6% e parece
tolerável; o nível de risco triplica.

### D4 — O par `stress_only` `[PEÇA CENTRAL]`

**Caminhos:**
- `reports/evidently/stress_only_scoring_month_06.html`
- `reports/evidently/stress_only_label_month_06.html`

**Na tela:** os dois abertos lado a lado, no mesmo instante. Abrem **offline** —
verificado, zero dependência de CDN.
**Por que:** o argumento inteiro em duas janelas.
**Pré-requisito:** nenhum, estão versionados. **Não** rodar `make drift-reports`
antes de gravar: gera em `_build/` e os arquivos versionados não mudam, mas
confunde se alguém olhar o `git status`.

### D5 — PSI x ganho: nem todo drift é o mesmo evento

**Caminho:** `reports/evidently/summary.md` §4, mês 6.
**Na tela:** `age` com PSI **0,314** e 3,29% de ganho, logo acima de
`NumberOfTimes90DaysLate` com PSI **0,302** e **28,47%** de ganho — e o impacto
invertido: 0,0104 contra 0,0859.
**Por que:** ordenar por PSI apresenta os dois como o mesmo evento. É um
argumento de dez segundos contra o painel padrão.

---

## 4. Esqueleto STAR — teto de 5:00

| seção | tempo | acumulado | conteúdo | herói / tomada |
|---|---|---|---|---|
| **Situation** | 0:30 | 0:30 | Modelo de crédito em produção, 150 mil linhas, 6,68% de inadimplência. A frase-tese, dita já aqui. | — |
| **Task** | 0:30 | 1:00 | Construir a camada que diz **quando** o modelo parou de servir. Não é treinar melhor, é saber que quebrou. | — |
| **Action** | 2:30 | 3:30 | Contrato com severidade e portão de ingestão → simulação de seis meses com ablação → relatórios do Evidently. | **D1** (0:20) · **B** (0:35) · **D2** (0:20) · **C** (0:35) · **D** (0:20) · folga 0:20 |
| **Result** | 1:15 | 4:45 | O 2x2 e a consequência de engenharia. | **D4** + **A** (0:45) · **D3** (0:15) · **D5** (0:15) |
| **Fecho** | 0:15 | 5:00 | Repetir a frase-tese. Monitoramento por rótulo é obrigatório, não complementar. | — |

**Regras de corte, em ordem** (se estourar):
1. Cortar o Pandera de **C**, manter só o Evidently. (-0:15)
2. Cortar **D** inteiro. (-0:20)
3. Cortar **D2**, a rampa — bonita, mas **D3** carrega o mesmo ponto com mais
   força. (-0:20)
4. Nunca cortar: **A**, **D4**, e a frase-tese.

**Observação de ritmo:** a seção Action é a que estoura. O erro fácil é explicar
o *como* — contrato, mecanismos, calibração. Ninguém precisa. Cada item da Action
existe para tornar o Result credível; se um não serve a isso, sai.

---

## 5. Descartados

Achados reais que não cabem em cinco minutos. Um dia cada, e o motivo.

| achado | por que fica de fora |
|---|---|
| **#2 — `coerce` desligado** (uint8: 300 vira 44) | Ótimo detalhe, zero tensão narrativa. Ninguém discorda depois de ouvir. |
| **#3 — check de frame vira falha por coluna** (4 duplicatas → 44 casos) | Exige explicar o `failure_cases` do Pandera. Custo de preparo alto demais para o retorno. |
| **#5 — reponderar piora a probabilidade** (Brier 0,1356 contra 0,0623 de um chute fixo) | **O corte mais doloroso.** É um resultado forte e contraintuitivo, mas exige explicar calibração *antes*, e calibração já é o que sustenta o 2x2. Dois conceitos novos no mesmo vídeo é um a mais. Candidato número 1 se sobrar tempo. |
| **#8 — a ablação refutou o desenho** (inflação quase inerte) | Vai como **uma frase** dentro de **B**, não como achado próprio. |
| **#10 (parte negativa) — o Evidently *não* tem o bug do dia 6** | Intelectualmente honesto e narrativamente morto: "procurei um problema e não achei" não sustenta trinta segundos. Fica no `findings.md`. |
| Par multivariado com resultado nulo (+0,0024 contra -0,0168) | Precisa do dia 8 para fazer sentido. Reavaliar depois do A/A. |
| Determinismo byte a byte do HTML (UUID aleatório) | Decisão de engenharia, não achado sobre o problema. Boa resposta *se perguntarem*. |

---

## 6. Pendências antes de gravar

- [ ] Rodar a lista de tomadas inteira, na ordem, numa árvore limpa
- [ ] Decidir se o resultado do dia 8 desloca o herói **D**
- [ ] Cronometrar a Action falada — é a que estoura
- [ ] Confirmar que os dois HTML do **D4** abrem offline na máquina da gravação
- [ ] Preparar a resposta para "por que não usou o preset do Evidently?"
      (resposta: seleção automática pode omitir o que a rubrica pede — `docs/evidently.md` §3)
