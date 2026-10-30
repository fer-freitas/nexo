---
name: dotnet-standards
description: Padrões .NET da empresa — estrutura da solução, camadas, nomenclatura, async, injeção de dependência, configuração, logging, erros, validação e dinheiro. Use ao escrever ou revisar qualquer código C#/.NET.
---
# Padrões .NET

> **A definir pela organização (F20):** o guia oficial de padrões substitui ou completa esta skill; as regras marcadas como **[portão]** são conferidas de forma determinística na esteira.

## Plataforma
- .NET LTS atual; `Nullable` e `ImplicitUsings` habilitados; `TreatWarningsAsErrors` no CI **[portão]**.
- Analisadores: `Microsoft.CodeAnalysis.NetAnalyzers` + regras da empresa em `.editorconfig` **[portão]**.
- `dotnet format --verify-no-changes` na esteira **[portão]**.

## Estrutura da solução
```
src/
  <Servico>.Api/              # transporte: controllers/minimal APIs, filtros, DI
  <Servico>.Application/      # casos de uso, portas (interfaces), DTOs, validação
  <Servico>.Domain/           # entidades, value objects, eventos, regras (ddd-tatico)
  <Servico>.Infrastructure/   # adaptadores: banco, SQS, SNS, Kafka, HTTP
  <Servico>.Worker/           # consumidores (ECS) — quando houver
  <Servico>.Lambda.<Nome>/    # funções Lambda — quando houver
tests/
  <Servico>.UnitTests/  <Servico>.IntegrationTests/  <Servico>.ArchitectureTests/
contracts/  docs/  infra/ (Terraform)
```
- Dependências: `Api → Application → Domain`; `Infrastructure → Application`; `Domain` não referencia nada de infraestrutura **[portão: NetArchTest]** (ver `architecture-rules`).

## Nomenclatura
- PascalCase para tipos e membros públicos; camelCase para locais e parâmetros; `_camelCase` para campos privados; interfaces com `I`; sufixo `Async` em métodos assíncronos.
- Nomes do negócio em inglês no código, conforme o glossário (`dominio-do-produto`).
- Casos de uso como `SolicitarEstornoHandler`/`RequestRefundHandler`: um por arquivo.

## Código
- Async de ponta a ponta; nunca `.Result`/`.Wait()`; propague `CancellationToken` em toda chamada de I/O.
- Injeção de dependência pelo container padrão; sem service locator; `IOptions<T>` para configuração tipada e validada no startup (`ValidateOnStart`).
- Classes `sealed` por padrão; `record` para DTOs e value objects.
- Métodos curtos (≤ ~30 linhas), um nível de abstração por método; sem classes "Utils"/"Helper" genéricas.
- Sem lógica em construtores; sem `static` mutável.
- Coleções expostas como `IReadOnlyCollection<T>`.

## Erros
- Exceções de domínio específicas (`EstornoForaDoPrazoException`) para violação de regra; `Result<T>` quando o erro é esperado e frequente — escolha um padrão por serviço e siga.
- Na API: `ProblemDetails` (RFC 9457) com código de erro do negócio; nunca stack trace para o cliente.
- Nunca `catch (Exception)` silencioso: registre com contexto e relance ou converta.

## Validação
- Entrada validada na borda (FluentValidation) antes do caso de uso; regras de negócio validadas no domínio.

## Dinheiro e datas
- Valores monetários com `decimal` (nunca `double`); arredondamento explícito (`MidpointRounding.ToEven` ou o da regra).
- Datas em UTC com `DateTimeOffset`; conversão para fuso só na apresentação; `TimeProvider` injetado para testar tempo.

## Logging
- `ILogger<T>` com templates estruturados (`"Estorno {EstornoId} solicitado"`), nunca interpolação; nada de dados pessoais ou segredos (`dados-e-lgpd`, `observabilidade-dotnet`).

## Checklist
- [ ] Camadas respeitadas
- [ ] Async com CancellationToken
- [ ] Configuração tipada e validada
- [ ] Erros no padrão do serviço e ProblemDetails na API
- [ ] decimal para dinheiro, DateTimeOffset UTC
- [ ] Sem avisos do compilador
