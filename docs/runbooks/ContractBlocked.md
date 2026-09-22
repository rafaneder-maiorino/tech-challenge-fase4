# ContractBlocked

**Severidade:** critical · **Origem:** `credit_monitor_scoring` · **Runbook do alerta homônimo**

## O que significa

O contrato de dados recusou o lote. Uma regra **bloqueante** falhou —
idade fora de [18, 110], código sentinela 96/98, renda negativa, missingness
aninhada quebrada ou dtype estrutural errado.

O pipeline **parou ali**: `stage_status` dos estágios seguintes vale `-1`
(pulado, não falhado) e **nenhuma métrica de drift, predição ou rótulo foi
publicada**. Isso é deliberado — calcular um PSI sobre linhas que o contrato
recusou produziria um número que parece medir a população e mede dado
corrompido.

## Primeiras verificações

### PromQL

```promql
# quais estágios falharam, em qual lote
stage_status{stage="contract"} == 0

# quais regras, com quantas linhas cada
contract_violations{severity="blocker"}

# quantas linhas foram para a quarentena
rows_quarantined
```

### LogQL (Loki)

```logql
{job="credit-monitor", status="failed"} |= "contract.blocked"

# a lista de regras e os ids (com hash) de algumas linhas afetadas
{job="credit-monitor", batch_id="<BATCH_ID>", stage="contract"} | json
```

## Causas prováveis

1. **A origem mudou o esquema.** Coluna com dtype diferente, coluna a mais ou a
   menos. É o caso mais comum e o mais fácil de confirmar: a regra
   `schema_columns_and_dtypes` aparece entre as violadas.
2. **Códigos administrativos voltaram.** As sentinelas 96/98 foram removidas no
   pré-processamento da etapa 1; se reaparecem, a extração a montante mudou.
3. **A missingness aninhada quebrou.** `nested_missingness` é a regra que pega
   mudança de origem que nenhuma outra pega — dependentes nulos sem renda nula
   significa que as duas colunas pararam de faltar pelo mesmo motivo.
4. **Um lote genuinamente ruim.** Acontece; é para isso que existe a quarentena.

## Ação

1. Abrir `data/quarantine/<batch_id>/rejections.jsonl` — um registro por
   rejeição, com regra, coluna e valor observado.
2. Confirmar qual regra disparou. Se for `schema_columns_and_dtypes`, falar com
   quem produz o dado **antes** de mexer em qualquer coisa aqui.
3. **Não** reprocessar o lote sem entender a causa. O portão existe justamente
   para impedir que dado assim chegue ao modelo.
4. Se o lote for legítimo e a regra estiver errada, a correção é mudar o
   contrato num commit revisado — não relaxar o limiar em produção.

## Quando retreinar

**Não retreinar por este alerta.** Um lote bloqueado não diz nada sobre o
modelo; diz que o dado não chegou no formato acordado. Retreinar com dado que o
contrato recusou é exatamente o que o contrato existe para impedir.

## O limiar, e a taxa de falso alarme medida

Binário: `stage_status{stage="contract"} == 0`. Não há limiar a calibrar — ou o
contrato aceitou o lote, ou não. As severidades de cada regra estão em
`src/credit_monitor/contracts/severity.py`, e a classificação
bloqueante/alerta é justificada regra a regra com os números da inspeção.
