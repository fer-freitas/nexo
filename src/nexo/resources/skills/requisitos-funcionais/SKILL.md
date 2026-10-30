---
name: requisitos-funcionais
description: Como detalhar requisitos funcionais depois do discovery — regras de negócio, fluxos principal e alternativos, exceções, permissões, mensagens e dados. Use ao transformar uma decisão de discovery em especificação para o time.
---
# Requisitos funcionais

Entrada: discovery aprovado. Saída: especificação da feature no formato do Nexo (ver `spec-writing`), pronta para virar histórias.

## Estrutura
```markdown
# <Feature>
## Objetivo (resultado de negócio e métrica)
## Escopo
## Fora de escopo
## Atores e permissões
| Ator | Pode | Não pode |
## Regras de negócio
| Id | Regra | Exemplo | Origem |
| RN-1 | Estorno só até 90 dias da compra | compra em 01/01, pedido em 02/04 → recusado | Contrato bandeira |
## Fluxo principal
1. …
## Fluxos alternativos e de exceção
- A1: <condição> → <comportamento>
- E1: <erro> → <mensagem ao usuário e o que acontece com os dados>
## Mensagens ao usuário
| Situação | Texto |
## Dados
| Campo | Obrigatório | Formato | Classe (dados-e-lgpd) | Origem |
## Requisitos não funcionais de negócio (ver requisitos-nao-funcionais-negocio)
## Dependências e sistemas vizinhos
## Critérios de aceite da feature (resumo; detalhe nas histórias)
```

## Regras de escrita
- Uma regra por linha, com exemplo concreto (valores reais de teste, nunca dados de cliente).
- Números em vez de adjetivos: "até 3 tentativas", não "algumas tentativas"; "em até 2 segundos", não "rápido".
- Toda exceção diz o que o usuário vê **e** o que acontece com os dados (nada gravado, gravado como pendente, estornado…).
- Permissões explícitas por ator; "qualquer usuário" precisa ser uma decisão, não omissão.
- Termos do negócio conforme o glossário (`dominio-do-produto`).
- Fora de escopo é obrigatório: evita discussão depois.

## Checklist
- [ ] Toda regra tem id, exemplo e origem
- [ ] Fluxos alternativos e de erro cobertos
- [ ] Mensagens definidas
- [ ] Dados classificados
- [ ] Fora de escopo escrito
- [ ] Nada ambíguo ("etc.", "adequado", "rápido", "quando necessário")
