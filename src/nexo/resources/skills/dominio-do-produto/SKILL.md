---
name: dominio-do-produto
description: Como descrever e consultar o domínio de negócio de um produto — glossário, contextos delimitados, regras invioláveis e sistemas vizinhos. Use no discovery, no desenho técnico, ao nomear classes e eventos ou quando um termo do negócio aparecer sem definição.
---
# Domínio do produto

Cada produto tem uma skill própria `dominio-<produto>` no escopo do squad (`squads/<squad>/extensions/skills/`), criada a partir deste modelo e mantida pelo PM com o tech lead. O agente writer propõe atualizações quando uma história muda o domínio.

## Estrutura obrigatória da skill `dominio-<produto>`

```markdown
# Domínio: <produto>

## Em uma frase
<o que o produto faz, para quem e qual resultado de negócio entrega>

## Glossário
| Termo | Significado no negócio | Nome no código | Não confundir com |
|---|---|---|---|
| Estorno | Devolução total ou parcial de uma compra aprovada | `Refund` | Cancelamento (antes da captura); contestação (`Chargeback`, iniciada pelo emissor) |

## Contextos delimitados
| Contexto | Responsabilidade | Dono (squad) | Linguagem própria |
|---|---|---|---|

## Regras invioláveis
- R1: <regra> — origem: <norma, contrato, decisão> — onde é garantida: <serviço/classe/teste>

## Estados e ciclo de vida
<diagrama de estados das entidades centrais (Mermaid stateDiagram)>

## Eventos de negócio
| Evento | Quando acontece | Produtor | Consumidores |
|---|---|---|---|

## Sistemas vizinhos
| Sistema | O que trocamos | Integração (API, SQS, SNS, Kafka) | Dono |

## Métricas de negócio
<indicadores que o produto move e onde são medidos>
```

## Regras de uso
- Termo novo do negócio só entra no código depois de estar no glossário, com o nome no código definido. Nome em inglês no código, significado em português no glossário.
- Uma palavra, um significado por contexto. Se o mesmo termo muda de sentido entre contextos, registre em "Não confundir com".
- Regra inviolável sempre aponta onde é garantida (código e teste). Regra sem teste é pendência do tech lead.
- Eventos de negócio no passado (`PagamentoAprovado`, `EstornoSolicitado`) e com dono único.
- Nunca coloque dados reais de clientes em exemplos; use dados fictícios.

## Checklist ao consultar
- [ ] Os nomes que vou usar (classes, eventos, tópicos, filas) estão no glossário?
- [ ] A mudança afeta alguma regra inviolável? Se sim, o teste dela continua passando?
- [ ] Estou mexendo no contexto certo ou invadindo outro contexto delimitado?
- [ ] Algum sistema vizinho precisa ser avisado (contrato, evento, versão)?
