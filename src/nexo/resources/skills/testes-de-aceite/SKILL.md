---
name: testes-de-aceite
description: Como os critérios de aceite em Gherkin viram testes automatizados antes do código — mapeamento cenário → teste, ids AC-n, rastreabilidade e testes travados. Use ao escrever testes a partir de uma história (agente qa-author) ou ao revisar a cobertura de critérios.
---
# Testes de aceite

No Nexo o agente `qa-author` escreve os testes de aceite **antes** da implementação, a partir do Gherkin da história. Depois de aprovados, os testes ficam travados: o dev corrige o código, não o teste.

## Mapeamento
| Gherkin | Teste .NET |
|---|---|
| `@AC-1 Cenário: Estorno dentro do prazo é aceito` | `AC1_Estorno_dentro_do_prazo_e_aceito()` com `// @AC-1` |
| `Dado …` | Arrange (builders, dados fictícios) |
| `Quando …` | Act (uma ação pelo caso de uso ou pela API) |
| `Então …` / `E …` | Asserts do resultado observável |
| `Esquema do Cenário` + `Exemplos` | `[Theory]` com `[InlineData]` por linha |

## Onde fica o teste
- Regra de negócio pura → teste unitário do domínio/caso de uso.
- Envolve API, banco, fila ou evento → teste de integração (`testes-de-integracao`).
- O mesmo critério pode ter os dois, mas precisa de pelo menos um.

## Rastreabilidade
- Todo `AC-n` da história tem teste com a marca `@AC-n`; o Nexo reprova a história com critério sem teste ou com teste falhando **[portão]**.
- Critério técnico `CT-n` com verificação `teste` segue a mesma regra (`// @CT-n`).

## Regras
- O teste falha antes da implementação (vermelho) pelo motivo certo.
- Dados concretos do cenário, fictícios.
- Não testar detalhes de implementação; testar o que o critério promete.

## Checklist
- [ ] Todos os AC-n e CT-n (teste) com teste marcado
- [ ] Esquemas viraram Theory
- [ ] Testes vermelhos antes do código
