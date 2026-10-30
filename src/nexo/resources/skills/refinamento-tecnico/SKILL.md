---
name: refinamento-tecnico
description: Como conduzir o refinamento técnico de uma história no modelo único — entendimento, critérios técnicos e de teste, performance, tarefas, estimativa, riscos e dependências — até passar na Definition of Ready. Use na sessão de refinamento com o time.
---
# Refinamento técnico

Resultado obrigatório: `refinamento.yaml` da história no **modelo único** (`definition-of-ready`) e o portão `nexo ready` aprovado. O modelo é o mesmo em todos os squads para que a qualidade do refinamento não dependa de quem refinou.

## Roteiro da sessão (30–60 min por feature)
1. **Entendimento (PM explica, time pergunta):** problema, regras, critérios de aceite. Dúvida sem resposta vira pergunta registrada com dono.
2. **Desenho (tech lead apresenta o SDD):** componentes, fluxos, contratos, dados.
3. **Critérios técnicos** (`criterios-tecnicos-de-aceite`).
4. **Critérios de teste**, na ordem do modelo:
   - unitário: cobertura mínima nas linhas alteradas;
   - integração: um cenário por integração tocada (fila, tópico, banco, API);
   - contrato: se cria ou muda API ou evento;
   - **performance: carga, duração e limites (p95, p99, erro)**, ou justificativa de não aplicar;
   - mutação: score mínimo;
   - segurança: nível de bloqueio e itens específicos.
5. **Observabilidade:** logs, métricas e alarmes.
6. **Deploy e mudança:** estratégia, rollback, feature flag, risco da mudança e se é candidata a mudança padrão.
7. **Tarefas:** quebra técnica (ver `task-breakdown`); cada tarefa ≤ 1 dia, com arquivos previstos.
8. **Estimativa:** pontos da história (1, 2, 3, 5, 8). Acima de 8: fatiar.
9. **Riscos e dependências:** com dono e data.
10. **Portão:** `nexo ready US-x`. Reprovado: completar e rodar de novo.

## Regras
- Não inventar números de performance: vêm do negócio (`requisitos-nao-funcionais-negocio`). Faltou número? Pergunta registrada ao PM.
- Spike quando a incerteza impede estimar; com prazo e pergunta.
- História que depende de outro time só fica pronta com a dependência acordada.
- Tudo registrado no Nexo; nada fica só na conversa.

## Checklist de saída
- [ ] refinamento.yaml completo
- [ ] Performance com números ou justificativa
- [ ] Tarefas com arquivos previstos
- [ ] Estimativa e riscos
- [ ] `nexo ready` aprovado
