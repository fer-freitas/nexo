---
name: task-breakdown
description: Regras do Nexo para quebrar features em histórias e tarefas paralelizáveis por agentes. Use ao gerar backlog.
---
# Quebra de backlog
- História entrega valor; tarefa é unidade técnica que um agente conclui em uma sessão (≤ ~400 linhas, ≤ 8 arquivos, 1 PR revisável).
- Contrato primeiro; frontend e backend paralelizam depois do contrato.
- Declare `files_hint` realistas: é o que permite paralelizar sem conflito.
- Migrações e infraestrutura são tarefas separadas e exigem portão humano.
- Testes de aceite são escritos pelo QA a partir do Gherkin (F3) — não crie tarefa só para "escrever testes de aceite".
