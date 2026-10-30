---
name: aws-mensageria-sqs-sns
description: Como usar SQS e SNS com .NET — filas padrão e FIFO, fan-out SNS→SQS, filter policies, DLQ, retry, visibility timeout, lotes, atributos e idempotência. Use ao publicar ou consumir mensagens nesses serviços.
---
# SQS e SNS

## Topologia padrão
```
Produtor → SNS <dominio>-<evento> ─┬─ filter → SQS <consumidor-a> (+ DLQ)
                                    └─ filter → SQS <consumidor-b> (+ DLQ)
```
- Evento de domínio publicado no SNS; cada consumidor tem **sua** fila inscrita (fan-out). Consumidor nunca lê a fila de outro.
- Comando ponto a ponto pode ir direto para uma SQS do dono.

> **A definir pela organização:** convenção de nomes de filas e tópicos, tags obrigatórias e criptografia (KMS gerenciada ou própria).

## Filas
- Toda fila com DLQ e `maxReceiveCount` (ex.: 5); alarme na DLQ > 0 e na idade da mensagem mais antiga.
- `VisibilityTimeout` ≥ 6× o tempo de processamento (Lambda: timeout da função).
- Long polling (`ReceiveMessageWaitTimeSeconds = 20`).
- Retenção coerente com o tempo de recuperação (padrão 4 dias; até 14).
- Criptografia (SSE-KMS) ligada; política de acesso restrita aos produtores/consumidores.

## FIFO
- Só quando a ordem importa **por chave** (`MessageGroupId` = id da conta/compra); deduplicação por `MessageDeduplicationId`.
- Vazão limitada; não use FIFO "por garantia".

## Publicação
- Envelope e contrato definidos (`contratos-de-api-e-eventos`); atributos de mensagem para `tipo`, `versao` e `correlation_id` (usados em filter policies e rastreio).
- Publicar em lote quando possível (`PublishBatch`/`SendMessageBatch`).
- Publicação consistente com o banco: outbox (`padroes-de-projeto`).
- Tamanho máximo 256 KB: payload grande vai para S3 e a mensagem leva a referência.

## Consumo
- **Idempotente sempre** (entrega at-least-once): chave de idempotência pelo id do evento (`resiliencia-e-idempotencia`).
- Apagar a mensagem só depois de processar com sucesso.
- Erro transitório: deixar voltar (visibilidade) ou `ChangeMessageVisibility` com backoff; erro permanente: registrar e mandar para a DLQ.
- Lambda: falha parcial de lote (`aws-lambda-dotnet`).

## Reprocessamento
- Redrive da DLQ documentado no runbook, após corrigir a causa; nunca redrive cego.

## Checklist
- [ ] Fan-out com uma fila por consumidor
- [ ] DLQ, alarmes e visibility timeout corretos
- [ ] Contrato e atributos de mensagem
- [ ] Consumo idempotente e exclusão após sucesso
- [ ] Criptografia e política de acesso restrita
