---
name: deploy-aws
description: Estratégias de deploy e rollback na AWS — canário e linear em Lambda com aliases, rolling e blue/green em ECS, feature flags, migrações compatíveis e critérios automáticos de rollback. Use ao definir a estratégia de deploy no refinamento ou ao preparar uma implantação.
---
# Deploy na AWS

## Escolha da estratégia
| Situação | Estratégia |
|---|---|
| Lambda com tráfego relevante | Canário via alias (ex.: 10% por 10 min, depois 100%) com CodeDeploy e alarmes |
| API em ECS, mudança comum | Rolling com circuit breaker de deploy e rollback automático |
| API em ECS, mudança de risco | Blue/green (CodeDeploy) com tráfego de teste e troca rápida |
| Funcionalidade nova para o usuário | Deploy desligado + **feature flag** ligada gradualmente |
| Worker/consumidor | Rolling; garantir idempotência (mensagens podem ser reprocessadas na troca) |

## Rollback
- Definido **antes** do deploy (campo `deploy.rollback` do refinamento).
- Automático por alarme: taxa de erro, latência p95 ou DLQ acima do limite durante o canário.
- Lambda: voltar o alias para a versão anterior. ECS: voltar para a task definition anterior / trocar o target group.
- Banco: migrações **expand/contract** (`persistencia`) para o código anterior continuar funcionando; rollback nunca depende de desfazer migração.
- Feature flag: desligar é o rollback mais rápido.

## Por ambiente
- Dev: automático a cada merge. Homologação: automático ou com aprovação, com teste de carga. Produção: com a mudança aprovada (`gestao-de-mudanca`) e na janela.

## Regras
- Mesma estratégia testada em homologação antes de produção.
- Nunca deploy manual pelo console.
- Mudança de infraestrutura (Terraform) segue a mesma esteira, com plan aprovado.

## Checklist
- [ ] Estratégia e rollback no refinamento
- [ ] Alarmes que disparam rollback configurados
- [ ] Migrações compatíveis com a versão anterior
- [ ] Feature flag para funcionalidade nova ao usuário
