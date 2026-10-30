---
name: decisoes-de-arquitetura-adr
description: Quando e como registrar uma decisão de arquitetura (ADR) — contexto, opções, decisão, consequências e status. Use quando houver mais de uma alternativa técnica razoável ou uma decisão difícil de reverter.
---
# Decisões de arquitetura (ADR)

## Quando registrar
- Escolha entre serviços ou padrões (Lambda × ECS, SQS × Kafka, banco).
- Decisão difícil de reverter (modelo de dados, contrato público, particionamento).
- Exceção a um padrão da empresa.
- Decisão que outro time vai questionar daqui a seis meses.

Não registre o óbvio ou o que já é padrão da empresa.

## Formato (`docs/adr/NNNN-titulo-curto.md`)
```markdown
# ADR-0007: Kafka para eventos de conciliação
- Status: proposta | aceita | substituída por ADR-00xx | descartada
- Data: 2026-09-28
- Decisores: <tech lead, arquiteto>
## Contexto
<problema, restrições e números que importam>
## Opções
1. SNS + SQS — prós, contras, custo
2. Kafka (MSK) — prós, contras, custo
## Decisão
<opção escolhida e o porquê em poucas frases>
## Consequências
- Positivas:
- Negativas e riscos:
- O que muda para os times:
```

## Regras
- Uma decisão por ADR; numeração sequencial; nunca apague: substitua.
- Cite números (volume, latência, custo), não preferências.
- ADR citada no SDD e no PR que a implementa.
- O grafo do Nexo liga ADR aos arquivos que ela cita: cite os caminhos principais.
- O agente writer mantém o índice de ADRs atualizado.

## Checklist
- [ ] Contexto com números
- [ ] Pelo menos duas opções
- [ ] Consequências negativas escritas
- [ ] Status e decisores
