---
name: resiliencia-e-idempotencia
description: Como implementar resiliência em .NET — timeouts, retry com backoff e jitter, circuit breaker (Polly / Microsoft.Extensions.Http.Resilience), idempotência de APIs e consumidores, outbox e tratamento de erro transitório × permanente. Use ao chamar dependências ou processar mensagens.
---
# Resiliência e idempotência

Os números (timeouts, tentativas, limites) vêm do desenho (`resiliencia-no-desenho`).

## HttpClient
```csharp
services.AddHttpClient<IAntifraudeClient, AntifraudeClient>(c => c.BaseAddress = new(opts.Url))
    .AddStandardResilienceHandler(o =>
    {
        o.AttemptTimeout.Timeout = TimeSpan.FromMilliseconds(800);
        o.Retry.MaxRetryAttempts = 2;                        // backoff exponencial com jitter por padrão
        o.CircuitBreaker.FailureRatio = 0.5;
        o.CircuitBreaker.MinimumThroughput = 20;
        o.TotalRequestTimeout.Timeout = TimeSpan.FromSeconds(3);
    });
```
- Retry só para erros transitórios (timeout, 5xx, 429). Nunca para 4xx de validação.
- Retry em uma camada só; SDK da AWS já tem retry: não empilhe outro por cima sem necessidade.

## Idempotência de API
- `POST` que cria recurso exige cabeçalho `Idempotency-Key`.
- Guardar `(chave, hash do corpo, resposta)` com expiração (ex.: 24 h). Mesma chave + mesmo corpo → mesma resposta; mesma chave + corpo diferente → 422.
- A gravação da chave e do recurso na mesma transação (ou condição de unicidade).

## Idempotência de consumidor
- Chave = id do evento/mensagem (ou chave de negócio).
- Tabela de mensagens processadas com restrição de unicidade, gravada **na mesma transação** do efeito; ou operação naturalmente idempotente (upsert condicional, `ConditionExpression` no DynamoDB).
```csharp
await using var tx = await _db.BeginTransactionAsync(ct);
if (!await _processadas.TentarRegistrarAsync(evento.Id, ct)) return;   // já processada
await _casoDeUso.ExecutarAsync(evento, ct);
await tx.CommitAsync(ct);
```

## Outbox
- Evento gravado na tabela `outbox` na mesma transação do agregado; publicador (worker/Lambda agendada) envia e marca como enviado; publicação pode repetir → consumidores idempotentes.

## Erros
| Tipo | Exemplos | Ação |
|---|---|---|
| Transitório | timeout, 503, throttling, conexão | retry com backoff; esgotou → fila/DLQ |
| Permanente | payload inválido, regra violada, 404 de recurso que deveria existir | não repetir; registrar e DLQ ou resposta de erro |

- Classifique explicitamente (`ErroPermanenteException`) para o consumidor decidir.

## Testes
- Testar: timeout aciona retry; circuito abre; mensagem duplicada não duplica efeito; chave de idempotência repetida devolve a mesma resposta.

## Checklist
- [ ] Timeouts e retry com os números do desenho
- [ ] Idempotência em POST e consumidores, na mesma transação
- [ ] Erros classificados
- [ ] Testes de duplicidade e de falha de dependência
