# Autoteste real com o Claude Code

Sem PR e sem revisão por outro fornecedor: a prova da M3 continua parcial.

- **Comando:** `nexo selftest --agent claude`, sem `--github`
- **Versões:** Nexo 0.1.0, Claude Code 2.1.314, gitleaks 8.30.1, macOS
- **Data:** 2026-10-30
- **Resultado:** `TUDO OK`, em 13 passos. Custo da história: R$ 1,29, mais as chamadas de planejamento

| Passo | Resultado |
|---|---|
| Preparar o projeto e o contexto | Passou |
| Planejar a feature, com dúvidas respondidas em 3 rodadas | Passou |
| Análise técnica e requisito técnico | Passou |
| Aprovar o plano | Passou com `--force`: prontidão de 99%, por requisito sem critério ligado |
| Explicar a tarefa e Definition of Ready | Passou |
| QA: testes de aceite escritos e travados | Passou |
| Dev: T-1-1 implementada | Passou |
| Bloqueio de política: comando fora da allowlist recusado (SEC-21) | Passou |
| Qualidade: lint, testes, SCA, segredos e SAST | Passaram |
| Portão | Pediu humano, confiança 0,78 de 0,95, e a aprovação liberou. No autoteste, quem pede pode aprovar |
| `check US-1-1` | Passou |

## O que este teste não provou

- **Revisão por outro fornecedor:** não aconteceu. Nenhuma outra rota estava disponível, e o portão exigiu humano no lugar, como previsto.
- **PR:** o autoteste rodou sem `--github`.
- **Outras ferramentas:** Devin e Copilot não rodaram.

## Defeitos encontrados

| Defeito | Situação |
|---|---|
| A falta do gitleaks só aparecia depois de gastar tokens | Corrigido: `dev run` e o autoteste avisam antes |
| `identity.mode` exigia login do GitHub no autoteste sem `--github` | Corrigido |
| Tarefa só de teste, coberta pelos testes do QA, ficava como `Pronto` no `status` com a história concluída | Corrigido; ainda não repetido com a ferramenta real |
