---
nexo_template: technical-design
version: 1
required_sections: [contexto, solucao-proposta, seguranca, observabilidade, implantacao-e-rollback, alternativas-consideradas]
locked_sections: [seguranca, observabilidade]
dor:
  - componentes e responsabilidades descritos
  - toda mudança de dado com migração reversível ou justificativa
  - controles de segurança ligados à classificação de dados
  - logs, métricas e traces definidos para os fluxos novos
  - plano de implantação e rollback
  - alternativas consideradas
---
# {{feature}} — Requisito técnico ({{mode}})

## Premissas
{{#premises}}
- **{{text}}**{{#why}} — por quê: {{why}}{{/why}}{{#if_false}} · se não se confirmar: {{if_false}}{{/if_false}}
{{/premises}}
{{^premises}}
- (nenhuma premissa registrada)
{{/premises}}

## 1. Contexto
{{context}}

## 2. Solução proposta
{{solution}}

## 3. Componentes e responsabilidades
{{#components}}
- **{{name}}**: {{responsibility}}{{#change}} · {{change}}{{/change}}{{#owner}} · dono: {{owner}}{{/owner}}
{{/components}}

## 4. Dados
{{#data}}
- **{{entity}}**: {{change}}{{#migration}} · migração: {{migration}}{{/migration}} · reversível: {{reversible}}
{{/data}}

## 5. Contratos e integrações
{{#contracts}}
- `{{path}}` ({{type}}){{#summary}} — {{summary}}{{/summary}}
{{/contracts}}

## 6. Segurança
{{#data_class}}
- Classe de dados: {{data_class}}
{{/data_class}}
{{#authz}}
- Autenticação/autorização: {{authz}}
{{/authz}}
{{#privacy}}
- Dados pessoais (LGPD): {{privacy}}
{{/privacy}}
{{#threats}}
- Ameaça: {{.}}
{{/threats}}

## 7. Observabilidade
{{#observability}}
- **{{flow}}**: logs {{logs}} · métricas {{metrics}} · traces {{traces}}{{#alerts}} · alertas {{alerts}}{{/alerts}}
{{/observability}}

## 8. Testes
{{testing}}

## 9. Implantação e rollback
{{#rollout}}
- Estratégia: {{rollout}}
{{/rollout}}
{{#flag}}
- Feature flag: {{flag}}
{{/flag}}
{{#rollback}}
- Rollback: {{rollback}}
{{/rollback}}

## 10. Alternativas consideradas
{{#alternatives}}
- **{{name}}**: {{summary}}{{#reason}} — descartada porque {{reason}}{{/reason}}
{{/alternatives}}

## 11. Impacto no código existente
{{#impacted}}
- Módulo afetado: `{{.}}`
{{/impacted}}
{{#hotspots}}
- Ponto crítico: `{{.}}`
{{/hotspots}}
{{#coverage}}
- Cobertura atual: {{coverage}}
{{/coverage}}
{{#characterization}}
- Testes de caracterização: {{characterization}}
{{/characterization}}
