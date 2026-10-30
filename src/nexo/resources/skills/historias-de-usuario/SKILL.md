---
name: historias-de-usuario
description: Como escrever e fatiar histórias de usuário — narrativa, fatiamento vertical (INVEST), tamanho, dependências e vínculo com os critérios. Use ao quebrar uma feature em histórias ou revisar o backlog.
---
# Histórias de usuário

## Formato
```
Título: <verbo no infinitivo + resultado>   ex.: Consultar estorno pelo app
Como <ator específico>
Quero <capacidade>
Para <valor de negócio>
Contexto: <o que o time precisa saber que não está na narrativa>
Critérios de aceite: AC-1..AC-n (ver criterios-de-aceite)
Regras de negócio: RN-x (da especificação)
Pontos: 1, 2, 3, 5 ou 8
```

## INVEST
| Letra | Pergunta | Se falhar |
|---|---|---|
| Independente | Dá para entregar sem esperar outra história? | Reordene ou junte |
| Negociável | Descreve o quê, não o como? | Tire a solução técnica da narrativa |
| Valiosa | Alguém fora do time percebe a entrega? | Junte com a história que dá valor |
| Estimável | O time consegue estimar? | Falta informação: volte ao PM ou faça spike |
| Pequena | Cabe na sprint (até 8 pontos)? | Fatie |
| Testável | Cada critério vira teste? | Reescreva os critérios |

## Como fatiar (vertical, nunca por camada)
- Por **regra de negócio**: primeiro o caso simples, depois as exceções.
- Por **variação de dado**: um tipo de produto, depois os demais.
- Por **operação**: consultar antes de alterar.
- Por **canal ou ator**: um canal primeiro.
- Por **qualidade**: funciona, depois performance em escala (com critério de performance explícito na segunda).
- **Nunca** "história do backend" e "história do front" separadas: cada história atravessa as camadas necessárias.

## Regras
- Ator específico ("cliente PJ", "analista de backoffice"), nunca "usuário" genérico quando houver mais de um perfil.
- Uma história, uma entrega de valor. "E" na narrativa costuma indicar duas histórias.
- Todo critério de aceite da feature aparece em pelo menos uma história.
- Dependência entre histórias é explícita e aponta a ordem.
- Spike (investigação) tem prazo e pergunta a responder; não entrega código de produção.
- Termos do glossário (`dominio-do-produto`).

## Checklist
- [ ] Narrativa completa com ator, capacidade e valor
- [ ] Passa no INVEST
- [ ] Critérios de aceite em Gherkin com ids
- [ ] Todas as regras e critérios da feature cobertos pelo conjunto de histórias
- [ ] Dependências e ordem definidas
