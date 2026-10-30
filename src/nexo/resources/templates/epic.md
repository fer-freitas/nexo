---
nexo_template: epic
version: 1
required_sections: [resultado-esperado, metricas-de-sucesso, patrocinador-e-horizonte]
locked_sections: [resultado-esperado, metricas-de-sucesso]
dor:
  - resultado de negócio mensurável
  - pelo menos 1 métrica com linha de base e meta
  - patrocinador definido
tracker:
  description: [resultado-esperado, metricas-de-sucesso, features]
---
# {{id}} — {{title}}

## Resultado esperado
{{outcome}}

## Métricas de sucesso
{{#metrics}}
- **{{name}}**: {{baseline}} → {{target}}{{#source}} (fonte: {{source}}){{/source}}
{{/metrics}}

## Features
{{#features}}
- {{id}} — {{title}} ({{state}})
{{/features}}

## Patrocinador e horizonte
{{#sponsor}}
- Patrocinador: {{sponsor}}
{{/sponsor}}
{{#horizon}}
- Horizonte: {{horizon}}
{{/horizon}}
