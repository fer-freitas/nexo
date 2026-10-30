---
name: solid-e-clean-code
description: SOLID e código limpo aplicados a C# — o que cada princípio exige na prática, sinais de violação e como corrigir. Use ao implementar, refatorar ou revisar classes e dependências.
---
# SOLID e código limpo

## SOLID na prática
| Princípio | Exige | Sinal de violação | Correção |
|---|---|---|---|
| **S** Responsabilidade única | Uma razão para mudar por classe | Classe com "And"/"Manager"/"Service" genérico; muitas dependências (> 5 no construtor) | Separar por responsabilidade de negócio |
| **O** Aberto/fechado | Novo comportamento sem editar o existente | `switch`/`if` por tipo que cresce a cada regra | Strategy, Chain of Responsibility, polimorfismo (`padroes-de-projeto`) |
| **L** Substituição de Liskov | Subtipo cumpre o contrato do tipo base | `NotImplementedException`, checagem de tipo concreto | Rever a hierarquia; composição |
| **I** Segregação de interfaces | Interfaces pequenas e específicas | Implementações com métodos vazios | Quebrar a interface por cliente |
| **D** Inversão de dependência | Domínio e aplicação dependem de abstrações | `new` de cliente HTTP/SDK no caso de uso | Porta no Application, adaptador na Infrastructure |

## Código limpo
- Nomes revelam intenção; sem abreviações; booleanos como pergunta (`EstaNoPrazo`).
- Funções pequenas, um nível de abstração, poucos parâmetros (> 3: objeto de parâmetro).
- Sem efeitos colaterais escondidos; comando ou consulta, não os dois.
- Sem duplicação de regra de negócio (a mesma regra em dois lugares diverge).
- Sem comentários explicando o óbvio; comente o **porquê** de decisões não evidentes.
- Sem código morto, flags mortas ou TODO sem item no tracker.
- Guard clauses no início em vez de `if` aninhado.
- Imutabilidade por padrão.

## Limites objetivos (conferidos por analisador)
> **A definir pela organização:** valores oficiais. Sugestão: complexidade ciclomática ≤ 10 por método, ≤ 30 linhas por método, ≤ 5 dependências por construtor.

## Checklist
- [ ] Cada classe com uma responsabilidade clara
- [ ] Extensão por novas classes, não por novos `if`
- [ ] Dependências por abstração
- [ ] Nomes do negócio e funções pequenas
