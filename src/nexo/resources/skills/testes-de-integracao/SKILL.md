---
name: testes-de-integracao
description: Testes de integração e de contrato em .NET — WebApplicationFactory, Testcontainers, LocalStack para SQS/SNS/Lambda, Kafka em contêiner, banco real, testes de contrato de API e eventos. Use quando a história toca API, fila, tópico, banco ou serviço externo.
---
# Testes de integração

Cada cenário `CI-n` do `refinamento.yaml` vira um teste aqui.

## Ferramentas
| Integração | Como testar |
|---|---|
| API HTTP própria | `WebApplicationFactory<Program>` com dependências reais em contêiner |
| Banco relacional | Testcontainers (mesmo motor e versão de produção) |
| DynamoDB, SQS, SNS, S3, Lambda | LocalStack via Testcontainers |
| Kafka | Testcontainers Kafka (+ schema registry quando usado) |
| API externa | WireMock.Net com respostas do contrato (inclui erro e lentidão) |

## Padrões
- Contêineres por coleção de testes (`IAsyncLifetime` + `ICollectionFixture`), dados limpos entre testes.
- Nada de ambiente compartilhado ("hml") em teste automatizado da esteira.
- Esperas por condição com timeout (polling), nunca `Thread.Sleep` fixo.
- Cada teste arruma seus dados; nenhuma dependência de ordem.

## O que cobrir (mínimo)
1. Fluxo principal de ponta a ponta no serviço (API → banco → evento publicado).
2. Consumo de mensagem: processa, grava, publica e **apaga** a mensagem.
3. **Duplicidade:** mesma mensagem duas vezes não duplica efeito.
4. **Falha de dependência:** timeout/5xx → retry → DLQ (ou resposta degradada).
5. Mensagem inválida vai para a DLQ/DLT sem travar o consumo.
6. Autorização por recurso (outro cliente não acessa).

## Contrato
- Produtor: o que publica/responde valida contra o esquema em `contracts/`.
- Consumidor: consegue ler os exemplos do contrato (incluindo campos extras desconhecidos).
- Mudança de contrato roda os testes de contrato dos consumidores conhecidos.

## Checklist
- [ ] Um teste por cenário CI-n
- [ ] Duplicidade e falha de dependência testadas
- [ ] Contrato validado nos dois lados
- [ ] Sem sleeps fixos e sem ambiente compartilhado
