---
name: aws-ecs
description: Como construir e operar serviços .NET no Amazon ECS (Fargate) — imagem, task definition, health check, escala, configuração e segredos, graceful shutdown e workers. Use ao criar ou alterar uma API ou worker em ECS.
---
# Amazon ECS com .NET

## Imagem
- Multi-stage build; imagem base oficial `mcr.microsoft.com/dotnet/aspnet` (chiseled/distroless quando possível); usuário não root.
- Sem segredos na imagem; tag imutável (sha do commit); varredura de vulnerabilidades na esteira.

## Task definition
- CPU e memória medidos; limite de memória com folga para o GC (`DOTNET_GCHeapHardLimit` quando necessário).
- Logs em JSON para o CloudWatch/coletor padrão.
- Segredos pelo campo `secrets` (Secrets Manager/Parameter Store), nunca em `environment`.
- Task role com permissão mínima; execution role separada.

## Saúde
- `/health/live` (processo vivo) e `/health/ready` (dependências essenciais prontas) com `Microsoft.Extensions.Diagnostics.HealthChecks`.
- Health check do target group aponta para `ready`; timeouts e thresholds definidos.

## Escala
- Auto scaling por métrica relevante: CPU para API, profundidade/idade da fila para worker SQS, lag para consumidor Kafka.
- Mínimo de 2 tarefas em produção, em AZs diferentes.

## Encerramento
- Tratar `SIGTERM`: parar de receber, terminar o que está em andamento dentro do `stopTimeout`, confirmar mensagens processadas.
- `HostOptions.ShutdownTimeout` coerente com o `stopTimeout` da task.

## Workers (BackgroundService)
- Um `BackgroundService` por consumidor; loop com `CancellationToken`; exceção não derruba o host silenciosamente (logar e decidir).
- Long polling na SQS (`WaitTimeSeconds=20`), lote de até 10, apagar a mensagem só após sucesso.

## Deploy
- Rolling com `minimumHealthyPercent=100` ou blue/green (CodeDeploy) conforme risco (`deploy-aws`); circuit breaker de deploy ligado com rollback.

## Checklist
- [ ] Imagem não root, tag imutável, sem segredos
- [ ] Health checks live e ready
- [ ] Escala pela métrica certa, mínimo 2 tarefas
- [ ] Graceful shutdown testado
- [ ] Roles mínimas
