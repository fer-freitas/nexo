---
name: discovery-de-produto
description: Discovery do PM antes de pedir desenvolvimento — problema, evidências, hipóteses, métrica de sucesso, experimento e decisão. Use quando chegar uma demanda nova, uma ideia ou um pedido de área de negócio.
---
# Discovery de produto

Objetivo: decidir **se** vale construir e **o quê**, com evidência, antes de gastar desenvolvimento. O resultado alimenta `requisitos-funcionais` e `historias-de-usuario`.

## Etapas
1. **Problema:** quem sofre, com o quê, com que frequência e qual o custo hoje. Escreva sem solução: "clientes PJ não conseguem ver o estorno antes de 5 dias e ligam para a central", não "criar tela de estorno".
2. **Evidências:** dados de uso, atendimento, reclamações, pesquisa, benchmark, exigência regulatória. Cite a fonte e a data. Opinião não é evidência.
3. **Público e contexto:** segmentos afetados, volume, canais.
4. **Hipóteses:** "Acreditamos que <mudança> para <público> vai gerar <resultado>. Saberemos quando <métrica> chegar a <valor> em <prazo>."
5. **Métrica de sucesso:** uma principal (resultado de negócio) e até duas de proteção (o que não pode piorar: custo, reclamação, tempo de resposta).
6. **Opções de solução:** pelo menos duas, incluindo "não fazer" e a mais simples possível. Esforço e risco aproximados de cada uma (converse com o tech lead).
7. **Experimento (quando houver dúvida):** o menor teste que valida a hipótese (protótipo, piloto com poucos clientes, feature flag, teste A/B).
8. **Decisão:** seguir, ajustar ou descartar, com o porquê. Registre no Nexo (`nexo discovery`).

## Artefato de saída (discovery.md)
```markdown
# Discovery: <título>
## Problema
## Evidências (fonte, data)
## Público e volume
## Hipóteses
## Métrica de sucesso e métricas de proteção
## Opções avaliadas (incluindo não fazer)
## Experimento e resultado
## Decisão e próximos passos
## Riscos, dependências e regulatório
```

## Regras
- Sem métrica de sucesso, não há discovery pronto.
- Exigência regulatória tem prazo e norma citados; ela dispensa a hipótese, mas não a métrica de proteção.
- Dados pessoais envolvidos? Aplique `dados-e-lgpd` já aqui.
- Envolva o tech lead cedo quando houver dúvida de viabilidade ou custo.

## Checklist de saída
- [ ] Problema descrito sem solução
- [ ] Pelo menos duas evidências com fonte
- [ ] Hipótese no formato acima
- [ ] Métrica principal e de proteção com valor-alvo e prazo
- [ ] Opção "não fazer" avaliada
- [ ] Decisão registrada
