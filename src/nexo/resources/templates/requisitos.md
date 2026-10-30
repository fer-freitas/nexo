---
nexo_template: functional-requirements
version: 1
required_sections: [requisitos, rastreabilidade]
dor:
  - todo RF com enunciado verificável, origem e prioridade MoSCoW
  - todo RF must/should ligado a pelo menos 1 critério de aceite
---
# {{feature}} — Requisitos funcionais

## 1. Requisitos
{{#requirements}}
### {{id}} — {{title}}
- **Enunciado**: {{statement}}
- **Prioridade**: {{priority}}
{{#source}}- **Origem**: {{source}}
{{/source}}{{#rules}}- **Regras**: {{rules}}
{{/rules}}{{#acceptance}}- **Critérios de aceite**: {{acceptance}}
{{/acceptance}}

{{/requirements}}

## 2. Regras de negócio
{{#rules}}
- **{{id}}**: {{text}}{{#example}} (ex.: {{example}}){{/example}}
{{/rules}}

## 3. Rastreabilidade
{{#trace}}
- {{rf}} → critérios {{acs}}{{#stories}} → histórias {{stories}}{{/stories}}
{{/trace}}
