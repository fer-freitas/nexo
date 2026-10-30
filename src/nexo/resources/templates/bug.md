---
nexo_template: bug
version: 1
required_sections: [resumo, evidencias]
dor:
  - reprodução (passos ou teste que falha)
  - evidência anexada (redigida)
  - severidade e classe definidas
dod:
  - teste de regressão que falhava e agora passa
  - causa raiz registrada
tracker:
  description: [resumo, reproducao, evidencias, impacto]
---
# {{id}} — {{title}}

> Severidade: **{{severity}}** · Origem: {{origin}} · Classe: {{class}} · História: {{story}} · Tentativa {{attempt}} de {{max_attempts}}

## Resumo
{{#failures}}
- {{.}}
{{/failures}}

## Reprodução
{{#steps}}
1. {{.}}
{{/steps}}

## Evidências
{{#evidence}}
- {{kind}}: {{ref}}
{{/evidence}}

## Impacto
{{impact}}

## Causa raiz
{{#root_cause}}{{root_cause}}{{/root_cause}}{{^root_cause}}_A preencher na correção._{{/root_cause}}
