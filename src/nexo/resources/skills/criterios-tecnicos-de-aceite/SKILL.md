---
name: criterios-tecnicos-de-aceite
description: Como o tech lead escreve critérios técnicos de aceite mensuráveis — comportamento técnico, performance, resiliência, segurança, observabilidade e custo — e como cada um será verificado. Use no refinamento técnico, junto com o refinamento.yaml.
---
# Critérios técnicos de aceite

Complementam os critérios de negócio (`criterios-de-aceite`) com o que o negócio não vê, mas a solução precisa garantir. Entram no `refinamento.yaml` (`definition-of-ready`) com id `CT-n` e forma de verificação.

## Tipos e exemplos
| Tipo | Exemplo | Verificação |
|---|---|---|
| Comportamento técnico | CT-1: mensagem repetida com a mesma idempotency-key não gera segundo registro | teste de integração |
| Performance | CT-2: POST /estornos p95 ≤ 300 ms e erro ≤ 0,5% com 200 req/s por 10 min | teste de carga |
| Resiliência | CT-3: com o SNS indisponível, a mensagem volta para a fila e vai para a DLQ após 5 tentativas | teste de integração |
| Segurança | CT-4: a Lambda só tem `sqs:ReceiveMessage/DeleteMessage` na fila de estornos | revisão + varredura IaC |
| Observabilidade | CT-5: métrica `estorno_latencia_ms` e alarme de DLQ > 0 criados | revisão |
| Dados | CT-6: nenhum log contém CPF ou nome | teste automatizado de log |
| Compatibilidade | CT-7: o evento v2 é lido por consumidores da v1 | teste de contrato |
| Custo | CT-8: custo estimado ≤ R$ 800/mês no pico | revisão do SDD |

## Regras
- Mensurável: número, unidade e condição. "Performático" e "resiliente" não são critérios.
- Toda linha tem forma de verificação: `teste`, `revisao` ou `metrica` (observada após deploy).
- Performance sempre com carga, duração, percentil e taxa de erro; os números vêm de `requisitos-nao-funcionais-negocio`.
- Preferir `teste` a `revisao` sempre que possível: vira gate automático.
- Um critério técnico não repete o critério de negócio.

## Checklist
- [ ] Performance, resiliência, segurança e observabilidade cobertas quando aplicável
- [ ] Cada critério com id e verificação
- [ ] Nada vago
