---
name: persistencia
description: Padrões de persistência em .NET — EF Core com banco relacional, DynamoDB, migrações, transações, concorrência otimista, consultas e paginação. Use ao modelar tabelas, escrever repositórios ou migrações.
---
# Persistência

> **A definir pela organização:** bancos padrão por caso de uso e política de migração em produção.

## Geral
- Repositório por raiz de agregado (`ddd-tatico`); o domínio não conhece EF Core nem SDK.
- Toda operação com `CancellationToken`; sem consultas síncronas.
- Concorrência otimista em agregados alterados por mais de um fluxo (coluna de versão / `rowversion`; `ConditionExpression` no DynamoDB).
- Paginação por cursor em listas; limite máximo.
- Dados pessoais: criptografia/mascaramento e retenção conforme `dados-e-lgpd`.

## EF Core (relacional)
- Mapeamento por `IEntityTypeConfiguration<T>`; value objects como owned types/conversões; `decimal` com precisão explícita.
- `AsNoTracking` em leituras; projeção para DTO em consultas de leitura; evitar N+1 (`Include` consciente ou consulta dedicada).
- Migrações versionadas no repositório, **compatíveis com a versão anterior do código** (expand/contract): adicionar coluna nullable → código novo → backfill → tornar obrigatória depois.
- Migração é tarefa separada com portão humano (`task-breakdown`); script revisado; nunca `EnsureCreated` em produção.
- Índices para toda consulta de caminho crítico; revisar plano de execução em consultas novas.

## DynamoDB
- Modelagem pelos padrões de acesso (liste as consultas antes); chave de partição com boa distribuição.
- Escritas condicionais para idempotência e concorrência.
- Evitar `Scan` em caminho crítico; usar `Query` e índices secundários.
- TTL para dados com prazo de retenção.

## Transações
- Uma transação por agregado; eventos via outbox na mesma transação (`resiliencia-e-idempotencia`).

## Testes
- Repositórios testados contra banco real em contêiner (Testcontainers) ou DynamoDB Local/LocalStack, nunca só com mock.

## Checklist
- [ ] Mapeamento explícito e decimal com precisão
- [ ] Migração compatível com a versão anterior
- [ ] Concorrência otimista onde há disputa
- [ ] Índices para consultas críticas
- [ ] Testes com banco real
