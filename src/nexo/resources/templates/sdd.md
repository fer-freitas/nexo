---
nexo_template: sdd
version: 1
description: Especificação da feature (Spec-Driven Development) — fonte da verdade para testes e código.
required_sections: [objetivo, criterios-de-aceite, contratos, requisitos-nao-funcionais, classificacao-de-dados, estrategia-de-testes, riscos]
locked_sections: [criterios-de-aceite, classificacao-de-dados, estrategia-de-testes]
dor:
  - todo critério de aceite em Gherkin completo (Dado/Quando/Então) com id AC-n
  - contratos válidos (OpenAPI/JSON Schema/AsyncAPI) ou justificativa de "não se aplica"
  - NFR de desempenho, segurança e disponibilidade preenchidos
  - estratégia de testes cobrindo os 8 tipos (ou "n/a" justificado)
  - nenhuma dúvida bloqueante aberta
tracker:
  description: [objetivo, problema-e-contexto, escopo, criterios-de-aceite]
---
# {{id}} — {{title}}

> Documento gerado pelo Nexo a partir de `spec.yaml` (template `sdd`). Edite o YAML ou use `nexo plan feature`. Prontidão: **{{readiness}}%**{{#as_built}} · **as-built**{{/as_built}}

## 1. Objetivo
{{objective}}

## 2. Problema e contexto
{{problem}}

## 3. Escopo
{{#scope_in}}
- {{.}}
{{/scope_in}}

## 4. Fora de escopo
{{#scope_out}}
- {{.}}
{{/scope_out}}

## 5. Critérios de aceite
{{#acceptance}}
- **{{id}}** {{title}}
  - Dado {{given}}
  - Quando {{when}}
  - Então {{then}}
{{#requirements}}  - Requisitos: {{requirements}}
{{/requirements}}
{{/acceptance}}

## 6. Contratos
{{#contracts}}
- `{{path}}` ({{type}}){{#summary}} — {{summary}}{{/summary}}
{{/contracts}}
{{#contracts_not_applicable}}
Não se aplica: {{contracts_not_applicable}}
{{/contracts_not_applicable}}

## 7. Requisitos não funcionais
{{#nfr}}
- **{{category}}**: {{requirement}}
{{/nfr}}

## 8. Classificação de dados
{{#data_class}}**{{data_class}}**{{/data_class}}

## 9. Dependências
{{#dependencies}}
- {{.}}
{{/dependencies}}
{{#dependencies_none}}
Nenhuma.
{{/dependencies_none}}

## 10. Estratégia de testes
{{#test_strategy}}
- **{{kind}}**: {{approach}}
{{/test_strategy}}

## 11. Riscos
{{#risks}}
- **{{area}}**: {{description}}
{{/risks}}
{{^risks}}
{{#risks_reviewed}}
Revisados: nenhum risco relevante.
{{/risks_reviewed}}
{{/risks}}

## 12. Premissas e dúvidas
{{#assumptions}}
- Premissa: {{.}}
{{/assumptions}}
{{#questions}}
- Dúvida {{id}} ({{status}}): {{text}}
{{/questions}}
