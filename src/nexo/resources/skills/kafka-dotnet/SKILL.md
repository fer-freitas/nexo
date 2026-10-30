---
name: kafka-dotnet
description: Como produzir e consumir eventos Kafka com .NET (Confluent.Kafka) — tópicos, chaves e partições, esquema, garantias de entrega, commits de offset, retry e dead letter topics, consumidores em ECS e monitoramento de lag. Use ao criar ou alterar produtores ou consumidores Kafka.
---
# Kafka com .NET

> **A definir pela organização:** cluster (MSK/Confluent), schema registry, autenticação (IAM/SASL/mTLS), convenção de nomes de tópicos e grupos, retenção padrão.

## Tópicos
- Nome: `<dominio>.<entidade>.<evento>.v<n>` (ex.: `pagamentos.estorno.solicitado.v1`).
- Partições dimensionadas pelo pico de consumo (vazão por partição × consumidores); aumentar partições muda a distribuição de chaves: planeje.
- Retenção e compactação definidas no desenho.

## Produtor
- **Chave = id do agregado** (ex.: `compraId`) para ordem por entidade.
- `acks=all`, `enable.idempotence=true`; `linger.ms`/lote ajustados para vazão.
- Esquema no schema registry (Avro/JSON Schema/Protobuf) com compatibilidade `BACKWARD`.
- Publicação consistente com o banco via outbox.
- Cabeçalhos: `correlation_id`, `tipo`, `versao`, `origem`.

## Consumidor
- Um grupo por aplicação consumidora; consumidor de longa duração em ECS (não em Lambda, salvo event source mapping para volumes baixos).
- `enable.auto.commit=false`; commit do offset **depois** de processar com sucesso (at-least-once) → processamento idempotente obrigatório.
- Processamento em ordem por partição; paralelismo por partição, não por mensagem, quando a ordem importa.
- Rebalance: tratar `partitions revoked` (commitar o que foi processado, parar trabalho da partição).
- Erro transitório: retry com backoff limitado; esgotado, publicar em **tópico de retry** (`<topico>.retry.<n>`) e depois **DLT** (`<topico>.dlt`) com o motivo nos cabeçalhos. Nunca travar a partição indefinidamente.
- Mensagem inválida (não desserializa): direto para a DLT com log.

## Operação
- Métrica e alarme de **lag por partição**; throughput e taxa de erro.
- Reprocessamento: resetar offset do grupo ou consumir da DLT, com procedimento no runbook.

## Testes
- Integração com Testcontainers (Kafka) validando: publicação com chave, consumo idempotente, envio para DLT (`testes-de-integracao`).

## Checklist
- [ ] Chave correta e esquema registrado
- [ ] Produtor idempotente com acks=all
- [ ] Commit manual após processamento, consumo idempotente
- [ ] Retry topics e DLT
- [ ] Alarme de lag
