---
name: handoff-pm-tech-lead
description: O pacote de handoff do PM para o tech lead — o que precisa estar pronto, como entregar e como o tech lead devolve dúvidas. Use ao encerrar o detalhamento funcional e iniciar o refinamento técnico.
---
# Handoff PM → tech lead

No Nexo o handoff é automático: quando o PM aprova a parte de requisitos do pacote, o tech lead é avisado e recebe tudo anexado. Esta skill define o **conteúdo mínimo** do pacote.

## Pacote do PM
| Item | Onde fica |
|---|---|
| Discovery com decisão e métrica | `discovery.md` |
| Especificação funcional | `spec.md` (`requisitos-funcionais`) |
| Histórias com critérios Gherkin | `backlog.yaml` e tracker |
| Requisitos não funcionais de negócio | seção própria da spec |
| Dados pessoais e base legal | seção Dados da spec |
| Dependências e sistemas vizinhos | seção própria da spec |
| Perguntas em aberto | lista com dono e prazo |
| Fora de escopo | seção própria da spec |

## Regras
- Pergunta em aberto não bloqueia o handoff se tiver dono e prazo; bloqueia se afetar critério de aceite.
- O tech lead devolve dúvidas pelo Nexo (`request_clarification`), nunca por conversa solta: a resposta fica registrada e o próximo papel recebe.
- Nada é repassado por cópia manual entre ferramentas.
- Mudança de escopo depois do handoff volta para o PM e reabre a aprovação da parte de requisitos.

## Checklist do PM antes de aprovar
- [ ] Discovery com decisão
- [ ] Todas as regras de negócio com exemplo
- [ ] Histórias com critérios Gherkin
- [ ] Volume, pico e tempos em números
- [ ] Dados classificados
- [ ] Perguntas em aberto com dono

## Checklist do tech lead ao receber
- [ ] Entendi o problema e a métrica
- [ ] Números suficientes para critérios de performance
- [ ] Nenhuma regra ambígua (senão, pergunta registrada)
