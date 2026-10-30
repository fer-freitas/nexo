---
name: arquitetura-aws-eventos
description: Guia de escolha de arquitetura na AWS para serviços .NET — Lambda ou ECS, SQS, SNS ou Kafka, síncrono ou assíncrono, padrões de integração e custo. Use no desenho técnico e ao revisar uma solução que envolva esses serviços.
---
# Arquitetura AWS orientada a eventos

## Computação: Lambda ou ECS
| Critério | Lambda | ECS (Fargate) |
|---|---|---|
| Carga | Intermitente, orientada a evento | Contínua ou previsível |
| Duração | Até 15 min por execução | Sem limite |
| Latência sensível a cold start | Ruim sem provisioned concurrency / SnapStart | Boa |
| Conexões persistentes (banco, Kafka consumer de longa duração) | Evite | Adequado |
| Custo com uso alto e constante | Tende a ficar caro | Tende a ser menor |
| Operação | Mínima | Imagem, escala e saúde para cuidar |

Regra prática: consumidor de fila com picos → Lambda; API com tráfego constante, consumidor Kafka de alto volume ou processamento longo → ECS.

## Mensageria: SQS, SNS ou Kafka
| Necessidade | Use |
|---|---|
| Fila de trabalho ponto a ponto, retry e DLQ simples | SQS |
| Ordem por chave e deduplicação | SQS FIFO (limites de vazão) |
| Um evento para vários consumidores | SNS → várias SQS (fan-out) |
| Filtrar por atributo por consumidor | SNS com filter policy |
| Alto volume, reprocessar histórico, vários consumidores independentes, ordem por partição | Kafka (MSK) |
| Integração com plataforma de eventos corporativa existente | O padrão da empresa |

> **A definir pela organização:** plataforma de eventos padrão (MSK, Confluent), schema registry e convenções de nome de tópicos e filas.

## Padrões de integração
- **Comando × evento:** comando pede algo a um dono (`ProcessarEstorno`); evento avisa que algo aconteceu (`EstornoSolicitado`). Evento não espera resposta.
- **Transactional outbox:** gravar no banco e publicar evento de forma consistente.
- **Idempotência:** todo consumidor tolera mensagem repetida (at-least-once).
- **DLQ + reprocessamento:** toda fila tem DLQ, alarme na DLQ e forma documentada de reprocessar.
- **Síncrono só quando o usuário precisa da resposta agora;** o resto, assíncrono com status consultável.
- **Timeout em cascata:** timeout do chamador maior que o do chamado; nunca retry em todas as camadas.

## Custo (estime no SDD)
- Lambda: invocações × duração × memória. ECS: vCPU e memória por hora × tarefas.
- SQS/SNS: por requisição (agrupe com batch). Kafka: cluster + armazenamento + transferência.
- Logs e métricas também custam: evite log por mensagem em alto volume.

## Checklist
- [ ] Computação escolhida pelo critério acima e registrada
- [ ] Mensageria escolhida e justificada (ADR se houver dúvida)
- [ ] Idempotência, DLQ e reprocessamento definidos
- [ ] Custo mensal estimado no pico
