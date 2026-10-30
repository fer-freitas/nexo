---
nexo_template: adr
version: 1
required_sections: [contexto, decisao, consequencias]
---
# ADR-{{number}} — {{title}}

- **Status**: {{status}}
- **Data**: {{date}}
- **Feature**: {{feature}}

## Contexto
{{context}}

## Decisão
{{decision}}

## Consequências
{{consequences}}

## Alternativas consideradas
{{#alternatives}}
- **{{name}}**: {{summary}}{{#reason}} — {{reason}}{{/reason}}
{{/alternatives}}
