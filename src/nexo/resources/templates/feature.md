---
nexo_template: feature
version: 1
required_sections: [objetivo, problema, escopo, fora-de-escopo, documentos]
dor:
  - SDD com prontidão 100%
  - requisitos funcionais ligados a critérios de aceite
  - design técnico quando houver contrato, dado ou infraestrutura novos
dod:
  - todas as histórias em Concluido
  - documentos atualizados com o que foi entregue (as-built)
tracker:
  description: [objetivo, problema, escopo, fora-de-escopo, documentos]
---
# {{id}} — {{title}}

> {{#epic}}Épico: {{epic.id}} {{epic.title}} · {{/epic}}Classe de dados: **{{data_class}}** · Prontidão: **{{readiness}}%**{{#as_built}} · **as-built**{{/as_built}}

## Objetivo
{{objective}}

## Problema
{{problem}}

## Personas
{{#personas}}
- {{.}}
{{/personas}}

## Escopo
{{#scope_in}}
- {{.}}
{{/scope_in}}

## Fora de escopo
{{#scope_out}}
- {{.}}
{{/scope_out}}

## Documentos
{{#documents}}
- {{name}}: `{{path}}`
{{/documents}}

## Histórias
{{#stories}}
- {{id}} — {{title}} ({{points}} pts, {{state}})
{{/stories}}
