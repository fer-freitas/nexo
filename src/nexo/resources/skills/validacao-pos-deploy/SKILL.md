---
name: validacao-pos-deploy
description: Como validar uma implantação em produção — smoke tests, health checks, métricas e SLOs durante o canário, critérios de rollback e registro do resultado na requisição de mudança. Use logo após um deploy.
---
# Validação pós-deploy

## Roteiro (primeiros 30 minutos)
1. **Health:** `/health/ready` verde em todas as tarefas/versões novas.
2. **Smoke:** testes automáticos curtos do fluxo principal em produção, com dados de teste permitidos (conta sintética) e sem efeito financeiro real.
3. **Métricas durante o canário:** taxa de erro, latência p95/p99, throttles, idade de fila/DLQ, lag Kafka — comparados com a versão anterior e com os limites do refinamento.
4. **Logs:** sem pico de `Error`/`Warning` novos.
5. **Negócio:** operações concluídas no ritmo esperado para o horário.
6. **Decisão:** seguir (100%) ou rollback.

## Critérios de rollback (automáticos quando possível)
- Erro acima do limite por 5 min, p95 acima do limite do refinamento, DLQ > 0 inesperado, alarme de negócio.
- Na dúvida, rollback primeiro e investigação depois.

## Registro
- Resultado e evidências (prints/links de métricas) na requisição de mudança e na história.
- Rollback vira incidente/bug com a causa (`nexo incident open` quando houver pilha de erro).

## Checklist
- [ ] Health e smoke verdes
- [ ] Métricas dentro dos limites durante o canário
- [ ] Decisão registrada na requisição de mudança
