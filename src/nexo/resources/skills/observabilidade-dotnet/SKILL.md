---
name: observabilidade-dotnet
description: Como instrumentar serviços .NET — logs estruturados, correlação, OpenTelemetry (traces e métricas), métricas de negócio, propagação em mensagens e health checks. Use ao implementar APIs, workers e Lambdas.
---
# Observabilidade em .NET

O que medir e alarmar vem do desenho (`observabilidade-no-desenho`); aqui está o como.

## Logs
- `ILogger<T>` com templates estruturados; saída JSON.
- Campos em todo log: `service`, `version`, `environment`, `correlation_id`, `trace_id`.
- Níveis: `Information` para eventos de negócio, `Warning` para anomalias recuperáveis, `Error` para falhas que precisam de ação. Sem `Debug` ligado em produção.
- Use `LoggerMessage` (source generator) nos caminhos quentes.
- **Nunca** dados pessoais, segredos, tokens ou payloads inteiros (`dados-e-lgpd`).
```csharp
[LoggerMessage(EventId = 1001, Level = LogLevel.Information, Message = "Estorno {EstornoId} solicitado para a compra {CompraId}")]
static partial void EstornoSolicitado(ILogger logger, Guid estornoId, Guid compraId);
```

## OpenTelemetry
- `AddOpenTelemetry().WithTracing(...).WithMetrics(...)` com instrumentação ASP.NET Core, HttpClient, AWS SDK e o exportador padrão da empresa.
- Recurso com `service.name`, `service.version`, `deployment.environment`.
- **Mensagens:** injetar o contexto (`traceparent`) nos atributos da mensagem SQS/SNS/cabeçalhos Kafka ao publicar e extrair ao consumir, para o trace atravessar a fila.

## Métricas de negócio
```csharp
private static readonly Meter Meter = new("Estornos");
private static readonly Counter<long> Solicitados = Meter.CreateCounter<long>("estornos_solicitados_total");
private static readonly Histogram<double> Latencia = Meter.CreateHistogram<double>("estorno_latencia_ms");
```
- Nomes em snake_case com unidade; labels de baixa cardinalidade (nunca id de cliente ou de transação).

## Health checks
- `/health/live` e `/health/ready` (`aws-ecs`).

## Checklist
- [ ] Logs estruturados com correlação e sem dados pessoais
- [ ] Traces com propagação em HTTP e mensagens
- [ ] Métricas de negócio definidas no desenho
- [ ] Labels de baixa cardinalidade
