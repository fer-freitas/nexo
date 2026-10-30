---
name: resiliencia-no-desenho
description: Decisões de resiliência no desenho — timeouts, retries, idempotência, DLQ, circuit breaker, degradação, limites de concorrência e dependências externas. Use no SDD e na revisão de soluções com integrações síncronas ou assíncronas.
---
# Resiliência no desenho

O dev implementa com `resiliencia-e-idempotencia`. Aqui se decide **o quê** e **com que números**.

## Para cada dependência, preencha
| Dependência | Tipo | Timeout | Retry (tentativas, backoff) | Circuit breaker | Se falhar |
|---|---|---|---|---|---|
| API Antifraude | HTTP síncrono | 800 ms | 2, exponencial com jitter | abre com 50% erro em 20 chamadas | responde 202 e processa depois |
| SNS estorno-eventos | publish | 2 s | 3 | — | mensagem volta à fila |

## Regras
- **Timeouts sempre**, e menores que o do chamador (orçamento de tempo de ponta a ponta).
- **Retry só em erro transitório** (timeout, 5xx, throttling), com backoff exponencial e jitter; nunca em 4xx de validação.
- **Retry em uma camada só.** Retry na API + no SDK + na fila multiplica a carga.
- **Idempotência** em todo consumidor e em todo POST que cria algo.
- **DLQ** em toda fila, com `maxReceiveCount` definido, alarme e processo de reprocessamento.
- **Visibility timeout da SQS** ≥ 6× o timeout da Lambda consumidora.
- **Limite de concorrência** para proteger dependências (reserved concurrency na Lambda, pool no ECS).
- **Degradação:** o que o usuário vê quando uma dependência cai (fila e processamento posterior, resposta parcial, mensagem clara).
- **Dependência externa sem SLA** = tratar como instável.

## Checklist
- [ ] Tabela preenchida para todas as dependências
- [ ] Idempotência e DLQ definidas
- [ ] Orçamento de tempo coerente de ponta a ponta
- [ ] Comportamento degradado descrito
