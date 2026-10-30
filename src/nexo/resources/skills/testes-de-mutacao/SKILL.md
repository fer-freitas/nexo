---
name: testes-de-mutacao
description: Teste de mutação em .NET com Stryker.NET — rodar só no que mudou, ler mutantes sobreviventes, melhorar os testes e atingir o score mínimo do refinamento. Use quando o portão de mutação reprovar ou ao revisar a força dos testes.
---
# Teste de mutação (Stryker.NET)

A mutação altera o código de propósito (troca `>` por `>=`, remove uma chamada, inverte um `if`) e verifica se algum teste falha. Mutante que **sobrevive** = teste fraco naquele ponto.

## Execução
- Na esteira, só nos arquivos alterados: `dotnet stryker --since:main` (ou `--mutate` com a lista de arquivos) **[portão]**.
- Score mínimo = `testes.mutacao.score_minimo` do refinamento (piso da org).
- Configuração em `stryker-config.json`: projeto de teste, limites (`thresholds.break` = score mínimo), exclusões justificadas (código gerado, DTOs sem lógica).

## Lendo o relatório
| Status | Significa | Ação |
|---|---|---|
| Killed | Um teste pegou | Nada |
| Survived | Nenhum teste percebeu | Escrever/fortalecer teste |
| No coverage | Nenhum teste passa por ali | Cobrir ou justificar |
| Timeout | Mutação gerou laço | Normalmente conta como morto |
| Equivalent | Mutação não muda o comportamento | Marcar como ignorado com justificativa |

## Como matar sobreviventes
- **Limites:** mutação `>` → `>=` sobreviveu? Falta teste exatamente no limite (dia 90 e dia 91).
- **Retorno ignorado:** assert só no "não lançou exceção"; asserte o valor.
- **Chamada removida:** o efeito (evento publicado, gravação) não é verificado.
- **Condição invertida:** falta o caso negativo.
- Nunca "matar" mutante com teste que asserta implementação interna; teste o comportamento.

## Checklist
- [ ] Score ≥ mínimo nos arquivos alterados
- [ ] Sobreviventes analisados; equivalentes justificados
- [ ] Testes novos focados em comportamento
