---
nexo_template: story
version: 1
required_sections: [narrativa, criterios-de-aceite]
locked_sections: [criterios-de-aceite]
dor:
  - narrativa completa (como / quero / para)
  - critérios de aceite ligados ao SDD (AC-n)
  - pontos estimados (no máximo 8)
dod:
  - testes de aceite escritos antes e passando
  - pipeline de qualidade e segurança verde
  - revisão independente aprovada
  - portão aprovado (automático ou humano)
tracker:
  description: [narrativa, contexto, tarefas]
  acceptance: criterios-de-aceite
---
# {{id}} — {{title}}

## Narrativa
{{#as_a}}**Como** {{as_a}}, **quero** {{i_want}}, **para** {{so_that}}.{{/as_a}}

## Contexto
{{context}}

## Critérios de aceite
{{#acceptance}}
- **{{id}}** {{title}} — Dado {{given}}; quando {{when}}; então {{then}}
{{/acceptance}}

## Tarefas
{{#tasks}}
- {{id}} — {{title}} ({{kind}})
{{/tasks}}

> Feature {{feature}} · {{points}} pts
