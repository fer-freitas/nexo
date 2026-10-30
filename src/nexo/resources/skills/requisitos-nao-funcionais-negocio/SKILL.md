---
name: requisitos-nao-funcionais-negocio
description: Como o PM levanta os requisitos não funcionais que vêm do negócio — volume, pico, tempo de resposta percebido, disponibilidade, prazo regulatório e janela de mudança — em números. Use no detalhamento da feature, antes do refinamento técnico.
---
# Requisitos não funcionais de negócio

O PM traz os números do negócio; o tech lead transforma em critérios técnicos e de performance (`criterios-tecnicos-de-aceite`). Sem esses números, o critério de performance da Definition of Ready não pode ser preenchido.

## Perguntas obrigatórias
| Tema | Pergunta | Exemplo de resposta |
|---|---|---|
| Volume | Quantas operações por dia e por mês? | 120 mil estornos/mês |
| Pico | Qual o pico e quando acontece? | 5x a média no dia 5 útil, 10h–12h |
| Crescimento | Quanto cresce em 12 meses? | +40% |
| Tempo percebido | Quanto o usuário aceita esperar? | resposta em até 2 s no app |
| Processamento assíncrono | Em quanto tempo o resultado precisa estar pronto? | estorno refletido em até 5 min |
| Disponibilidade | Qual o horário de uso e o impacto de ficar fora? | 24x7; fora do ar = ligação na central |
| Regulatório | Há norma, prazo ou relatório obrigatório? | prazo de resposta ao cliente em 10 dias |
| Auditoria | O que precisa ser rastreável e por quanto tempo? | quem aprovou o estorno, 5 anos |
| Janela de mudança | Há período em que não pode haver mudança? | congelamento no Black Friday |
| Custo | Há teto de custo de operação? | até R$ 0,01 por transação |

## Regras
- Sempre número e unidade. "Muito acesso" não serve.
- Se o negócio não souber, registre a estimativa, a fonte e o dono da confirmação; não deixe em branco.
- Pico é o que dimensiona a solução; média serve para custo.
- Registre no bloco "Requisitos não funcionais de negócio" da especificação.

## Checklist
- [ ] Volume médio e de pico com período
- [ ] Crescimento esperado
- [ ] Tempo de resposta aceitável (síncrono) ou prazo de processamento (assíncrono)
- [ ] Disponibilidade e impacto de indisponibilidade
- [ ] Regulatório e auditoria
- [ ] Janelas de congelamento
