---
name: mudanca-padrao
description: Critérios e evidências para uma mudança ser classificada como mudança padrão de baixo risco (standard change, aprovação simplificada) — reversibilidade, testes, performance, segurança, tamanho e histórico. Use ao classificar o risco no refinamento e ao preparar a requisição de mudança.
---
# Mudança padrão (baixo risco)

A mudança padrão é pré-aprovada: pula o comitê de mudanças quando atende critérios objetivos. A candidatura é indicada no refinamento (`mudanca.candidata_mudanca_padrao`) e confirmada com as evidências da entrega.

> **A definir pelo time:** critérios oficiais e quem concede. A lista abaixo é a proposta de critérios verificáveis; o Nexo confere os automáticos a partir do pacote de evidências.

## Critérios propostos
| Critério | Verificação | Automático |
|---|---|---|
| Reversível em minutos (alias, rollback, feature flag) sem perda de dados | plano de retorno + migração compatível | parcial |
| Sem mudança de contrato incompatível (API/evento) | teste de contrato | sim |
| Sem migração destrutiva de dados | análise da migração | parcial |
| Todos os critérios de aceite e técnicos com teste passando | rastreio AC/CT | sim |
| Cobertura das linhas alteradas ≥ mínimo | relatório | sim |
| Mutação ≥ mínimo | relatório | sim |
| Teste de carga dentro dos limites | relatório k6 | sim |
| Segurança sem achados bloqueantes | SAST/SCA/segredos/IaC | sim |
| Revisão independente aprovada | revisão do Nexo + humana | sim |
| Tamanho da mudança limitado (ex.: ≤ 400 linhas, ≤ 1 serviço) | diff | sim |
| Serviço sem incidente causado por mudança nos últimos 30 dias | histórico (DORA/incidentes) | sim |
| Deploy com canário/rolling e rollback automático por alarme | configuração | parcial |

## Regras
- Qualquer critério não atendido → mudança normal (`gestao-de-mudanca`).
- Mudança em área de risco (pagamentos, dados pessoais, autenticação) só como padrão se a política do time permitir.
- O pacote de evidências assinado acompanha a requisição.

## Checklist
- [ ] Candidatura indicada no refinamento
- [ ] Todos os critérios com evidência
- [ ] Pacote de evidências anexado
