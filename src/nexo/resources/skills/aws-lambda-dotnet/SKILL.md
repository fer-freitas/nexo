---
name: aws-lambda-dotnet
description: Como escrever funções AWS Lambda em .NET — handler, cold start, empacotamento, memória e timeout, consumo de SQS em lote com falha parcial, idempotência, configuração e testes. Use ao criar ou alterar uma Lambda.
---
# AWS Lambda com .NET

## Estrutura
- Projeto `<Servico>.Lambda.<Nome>` fino: recebe o evento, converte e chama o caso de uso do `Application`. Nenhuma regra de negócio no handler.
- Runtime gerenciado .NET LTS; `Amazon.Lambda.Annotations` ou handler clássico com DI configurado uma vez (fora do handler).
- Clientes AWS SDK, `HttpClient` e configuração criados na inicialização e reutilizados entre invocações.

## Cold start
- Prefira Native AOT ou SnapStart quando a latência importa; ReadyToRun no mínimo.
- Evite reflexão pesada e inicialização preguiçosa de muitos serviços.
- Provisioned concurrency só para APIs síncronas com meta de latência que o cold start viola (custo!).

## Memória e timeout
- Memória também define CPU: meça (Lambda Power Tuning) e registre a escolha.
- Timeout = maior duração esperada + folga; nunca o máximo "por segurança".
- Consumidor de SQS: `visibility timeout` da fila ≥ 6× o timeout da função.

## Consumo de SQS
- Lote (`batchSize`) com **falha parcial**: devolva `SQSBatchResponse` só com os ids que falharam; não falhe o lote inteiro.
```csharp
public async Task<SQSBatchResponse> Handler(SQSEvent evento, ILambdaContext ctx)
{
    var falhas = new List<SQSBatchResponse.BatchItemFailure>();
    foreach (var msg in evento.Records)
    {
        try { await _processar.ExecutarAsync(Mapear(msg), CancellationToken.None); }
        catch (Exception ex) when (ex is not ErroPermanenteException)
        {
            _logger.LogWarning(ex, "Falha ao processar {MessageId}", msg.MessageId);
            falhas.Add(new() { ItemIdentifier = msg.MessageId });
        }
    }
    return new SQSBatchResponse(falhas);
}
```
- Erro permanente (mensagem inválida): registre e **não** devolva para retry; mande para a DLQ por regra ou descarte com log de auditoria.
- Idempotência obrigatória (`resiliencia-e-idempotencia`).

## Concorrência
- `reserved concurrency` para proteger banco e dependências; `maximum concurrency` no event source da SQS.

## Configuração e segredos
- Variáveis de ambiente só para configuração não sensível; segredos no Secrets Manager/Parameter Store com cache (extensão ou SDK).
- IAM da função com o mínimo: só as ações e os recursos que usa (`iam-e-rede-aws`).

## Deploy
- Versões e alias; tráfego gradual (canário) no alias via esteira (`deploy-aws`).

## Testes
- Unitário do caso de uso sem Lambda; teste do handler com evento de exemplo; integração com LocalStack (`testes-de-integracao`).

## Checklist
- [ ] Handler fino e DI na inicialização
- [ ] Memória medida, timeout justificado
- [ ] Falha parcial de lote
- [ ] Idempotência e DLQ
- [ ] IAM mínimo e segredos fora de variáveis
