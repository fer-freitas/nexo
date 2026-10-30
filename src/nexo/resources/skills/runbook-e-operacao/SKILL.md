---
name: runbook-e-operacao
description: Como escrever e manter o runbook de um serviço — o que o serviço faz, dependências, dashboards, cada alarme com diagnóstico e ação, reprocessamento de DLQ, rollback e contatos. Use ao criar um serviço, um alarme ou após um incidente.
---
# Runbook e operação

Arquivo `docs/runbook.md` no repositório do serviço, mantido pelo agente writer a cada mudança relevante e revisado pelo squad.

## Estrutura
```markdown
# Runbook: <serviço>
## O que faz e para quem (1 parágrafo)
## Criticidade e SLOs
## Arquitetura (link para o SDD e diagrama C4 nível 2)
## Dependências (quem chamamos, quem nos chama) e contatos
## Dashboards e consultas úteis de logs
## Alarmes
### <nome do alarme>
- Significa:
- Impacto no cliente:
- Diagnóstico (passo a passo, consultas):
- Ação imediata:
- Escalar para:
## Procedimentos
- Reprocessar a DLQ <fila> (após corrigir a causa)
- Rollback (Lambda alias / ECS)
- Ligar/desligar feature flags
- Resetar offset do consumidor Kafka
## Histórico de incidentes (links)
```

## Regras
- Todo alarme tem seção no runbook; alarme sem ação clara é ruído e deve ser revisto.
- Procedimentos com comandos exatos e testados em homologação.
- Após incidente: atualizar diagnóstico e ação com o que se aprendeu (lição também vai para o cérebro).

## Checklist
- [ ] Todos os alarmes com diagnóstico e ação
- [ ] Procedimentos de DLQ, rollback e flags
- [ ] Contatos e escalonamento atualizados
