---
name: gestao-de-mudanca
description: Como preparar e abrir a requisição de mudança (change request) para produção na ferramenta de ITSM do time (ServiceNow) — classificação de risco, janela, plano de implantação, plano de retorno, testes e evidências, comunicação e aprovadores (CAB). Use ao preparar uma entrega para produção.
---
# Gestão de mudança

O Nexo monta o pacote de evidências assinado da entrega (`nexo audit evidence build`); a requisição de mudança usa esse pacote como anexo e fonte das informações. Com aprovação externa ligada (`approvals`), o Nexo abre e acompanha a mudança na ferramenta de ITSM.

## Tipos de mudança (ITIL)
| Tipo | Quando | Aprovação |
|---|---|---|
| Padrão | Baixo risco, repetível, com critérios pré-aprovados (`mudanca-padrao`) | Pré-aprovada; registro obrigatório |
| Normal | Demais mudanças planejadas | Comitê de mudanças (CAB) conforme o risco |
| Emergencial | Correção de incidente em produção | Aprovação rápida e revisão depois |

> **A definir pelo time:** ferramenta de ITSM, campos obrigatórios, categorias de risco, janelas, antecedência mínima e aprovadores.

## Conteúdo da requisição
| Campo | De onde vem |
|---|---|
| Título e descrição da mudança | Histórias/feature no tracker (`tracker-de-trabalho`) |
| Justificativa de negócio | Discovery / feature |
| Serviços e componentes afetados | SDD + `nexo graph impact` |
| Classificação de risco e tipo | `mudanca.risco` do refinamento + critérios do time |
| Janela de implantação | Calendário do time, fora de congelamento |
| Plano de implantação (passo a passo) | Estratégia de deploy (`deploy-aws`) |
| Plano de retorno (rollback) e tempo estimado | `deploy.rollback` do refinamento |
| Testes realizados e resultados | Pacote de evidências: unitário, integração, contrato, carga, mutação, segurança |
| Validação pós-implantação | `validacao-pos-deploy` |
| Impacto em clientes e comunicação | PM |
| Responsáveis e contatos durante a janela | Time |

## Regras
- Mudança só com a esteira verde até homologação e o teste de carga aprovado.
- Plano de retorno testado em homologação.
- Mudanças dependentes em sistemas diferentes: requisições relacionadas e ordem de implantação (`nexo program`).
- Após a janela: registrar o resultado (sucesso, sucesso parcial, retorno) com as evidências da validação pós-deploy.

## Checklist
- [ ] Todos os campos preenchidos a partir do pacote de evidências
- [ ] Plano de retorno com tempo estimado
- [ ] Janela fora de congelamento
- [ ] Aprovadores definidos
- [ ] Resultado registrado após a janela
