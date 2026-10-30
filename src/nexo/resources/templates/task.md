---
nexo_template: task
version: 1
required_sections: [descricao, criterios, arquivos-previstos]
dor:
  - tamanho XS, S ou M
  - no máximo 8 arquivos previstos
  - critério verificável por teste
tracker:
  description: [descricao, criterios, arquivos-previstos, dependencias]
---
# {{id}} — {{title}}

> História {{story}} · tipo {{kind}} · tamanho {{size}}

## Descrição
{{description}}

## Critérios
{{#acceptance}}
- {{.}}
{{/acceptance}}

## Arquivos previstos
{{#files_hint}}
- `{{.}}`
{{/files_hint}}

## Dependências
{{#depends_on}}
- {{.}}
{{/depends_on}}
{{^depends_on}}
Nenhuma — pode rodar em paralelo.
{{/depends_on}}
