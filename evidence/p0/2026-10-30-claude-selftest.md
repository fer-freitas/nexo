# Autoteste real com o Claude Code, parcial

- **Comando:** `nexo selftest --agent claude --dir <pasta descartável>`
- **Versões:** Nexo 0.1.0, Claude Code 2.1.314, macOS
- **Data:** 2026-10-30
- **Custo medido da história:** R$ 0,94, mais as chamadas de planejamento

| Passo | Resultado |
|---|---|
| Preparar o projeto e o contexto | Passou |
| Planejar a feature, com dúvidas respondidas em 3 rodadas | Passou |
| Análise técnica, requisito técnico e aprovação do plano | Passou |
| Explicar a tarefa e Definition of Ready | Passou |
| QA: testes de aceite escritos e travados | Passou |
| Dev: T-1-1 implementada | Passou |
| Bloqueio de política: comando fora da allowlist recusado (SEC-21) | Passou |
| Qualidade: lint, testes, SCA, SAST e padrão | Passaram |
| Qualidade: segredos | **Não rodou: gitleaks ausente nesta máquina. A história escalou, como manda o portão** |
| Portão, revisão por outro fornecedor e PR | **Não chegou** |

**Conclusão:** a prova não está completa. Falta repetir com o gitleaks instalado. O teste também revelou que a falta da ferramenta só aparecia depois de gastar tokens; agora `dev run` e o autoteste avisam antes de começar.
