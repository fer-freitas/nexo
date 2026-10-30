---
name: observabilidade-no-desenho
description: Como definir no desenho técnico os SLOs, logs, métricas, traces, alarmes e dashboards de uma solução antes do código. Use no SDD e no refinamento (seção observabilidade do refinamento.yaml).
---
# Observabilidade no desenho

Se não está definido no desenho, não é implementado. O dev implementa com `observabilidade-dotnet`.

## SLOs
- Para cada fluxo crítico: indicador (latência p95, taxa de sucesso, idade da mensagem), meta e janela. Ex.: "99,5% das solicitações de estorno respondidas com sucesso em 30 dias".
- Erro orçado (error budget) guia prioridade entre evolução e estabilidade.

## Sinais obrigatórios por tipo de componente
| Componente | Métricas | Alarmes |
|---|---|---|
| API (ECS) | requisições, erros 4xx/5xx, latência p50/p95/p99 | 5xx > 1% em 5 min; p95 acima da meta |
| Lambda | invocações, erros, duração, throttles, concorrência | erros > 1%; throttles > 0 |
| SQS | mensagens visíveis, idade da mais antiga, DLQ | idade > limite; DLQ > 0 |
| SNS | publicações, falhas de entrega | falhas > 0 |
| Kafka consumidor | lag por partição, erros | lag crescendo por 10 min |
| Negócio | operações concluídas, valores, recusas por motivo | queda abrupta vs mesmo horário |

## Logs
- Estruturados (JSON), com `correlation_id`, `trace_id`, serviço, versão e ambiente.
- Eventos de negócio relevantes com nome estável (`estorno.solicitado`).
- Nunca dados pessoais (`dados-e-lgpd`); retenção definida.

## Traces
- OpenTelemetry ponta a ponta; propagar contexto em HTTP e em mensagens (atributo da mensagem).

## Dashboards e runbook
- Um dashboard por serviço com os sinais acima e os SLOs.
- Todo alarme tem runbook (`runbook-e-operacao`) e dono.

## Checklist
- [ ] SLO por fluxo crítico
- [ ] Métricas e alarmes por componente
- [ ] Correlação em logs e traces
- [ ] Runbook para cada alarme
