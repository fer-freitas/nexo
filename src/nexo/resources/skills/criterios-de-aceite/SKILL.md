---
name: criterios-de-aceite
description: Como escrever critérios de aceite verificáveis em Gherkin (Dado/Quando/Então) com ids, casos de erro, limites e dados de exemplo, prontos para virar teste automatizado. Use ao escrever ou revisar histórias.
---
# Critérios de aceite

Cada critério vira um teste automatizado **antes** do código (o agente `qa-author` gera os testes a partir deles). Critério que não dá para testar não é critério.

## Formato
```gherkin
# language: pt
@US-3-1 @AC-1
Cenário: Estorno dentro do prazo é aceito
  Dado uma compra aprovada de R$ 150,00 feita há 30 dias
  Quando o cliente solicita o estorno total
  Então o estorno fica com status "Solicitado"
  E o evento EstornoSolicitado é publicado com o id da compra
```
- Um cenário por critério; id `AC-n` único na história.
- `Dado` = estado inicial com dados concretos; `Quando` = uma ação; `Então` = resultado observável (resposta, dado gravado, evento, mensagem).
- Use `Esquema do Cenário` + `Exemplos` para variações de dados e limites.

## O que cobrir
1. **Caminho feliz** de cada regra de negócio.
2. **Limites:** valor mínimo, máximo, zero, exatamente no limite (dia 90 e dia 91).
3. **Erros:** entrada inválida, sem permissão, recurso inexistente, dependência fora do ar.
4. **Estados:** o que acontece com o dado em cada erro (nada gravado? pendente?).
5. **Idempotência** quando houver mensagem ou retry: repetir a mesma solicitação não duplica.
6. **Mensagens ao usuário** exatas quando forem requisito.

## Regras
- Resultado observável, nunca "o sistema processa corretamente".
- Sem detalhes de implementação (nome de tabela, classe) no critério de negócio; esses vão nos critérios técnicos (`criterios-tecnicos-de-aceite`).
- Valores concretos e fictícios; nunca dados de cliente.
- Performance e volume **não** entram aqui como adjetivo: vão no modelo de refinamento como critério de performance mensurável (`definition-of-ready`).
- Português do Brasil, termos do glossário.

## Anti-padrões
| Evite | Prefira |
|---|---|
| "Então funciona corretamente" | "Então o status fica Aprovado e o saldo diminui R$ 150,00" |
| "Quando o usuário faz tudo" | uma ação por `Quando` |
| "Então é rápido" | critério de performance: p95 ≤ 300 ms com 200 req/s |
| Critério sem id | `@AC-n` sempre |

## Checklist
- [ ] Todo critério tem id e tag da história
- [ ] Caminho feliz, limites e erros cobertos
- [ ] Resultados observáveis e com valores
- [ ] Nenhum termo vago
