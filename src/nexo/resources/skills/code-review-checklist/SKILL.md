---
name: code-review-checklist
description: Checklist de revisão de código do Nexo e da empresa — o que foi pedido foi entregue, critérios e testes, arquitetura, padrões, segurança, dados, resiliência, observabilidade e o que bloqueia ou é sugestão. Use ao revisar histórias e PRs, feitos por IA ou por pessoas.
---
# Checklist de revisão

## 1. O que foi pedido foi entregue (bloqueia)
- Cada critério de aceite (`AC-n`) e técnico (`CT-n`) tem implementação **e** teste que o exercita de verdade.
- Casos de erro e limites cobertos; nada pedido ficou de fora; nada além do escopo.
- Contratos respeitados (rotas, payloads, códigos de status, eventos e versões).
- Se o problema é da especificação (ambígua/contraditória), marque `spec_issue` em vez de adivinhar.

## 2. Desenho e padrões
- Camadas e regras de arquitetura (`architecture-rules`, `dotnet-standards`) — bloqueia se violar.
- Regra de negócio no domínio (`ddd-tatico`); padrões usados com propósito (`padroes-de-projeto`).
- SOLID e código limpo (`solid-e-clean-code`) — sugestão, salvo quando dificulta manutenção evidente.

## 3. Segurança e dados (bloqueia)
- Autorização por recurso; entrada validada; nada concatenado em consultas (`secure-coding`).
- Nenhum segredo; nenhum dado pessoal em log, evento ou exceção (`dados-e-lgpd`).
- IAM mínimo nas mudanças de infraestrutura (`iam-e-rede-aws`).

## 4. Resiliência e operação
- Timeouts, retry, idempotência e DLQ conforme o desenho (`resiliencia-e-idempotencia`) — bloqueia em consumidores e POSTs.
- Logs, métricas e alarmes definidos no refinamento implementados (`observabilidade-dotnet`).

## 5. Testes
- Testes testam comportamento; sem asserts fracos; sem sleeps; dados fictícios.
- Integração para cada integração tocada; carga quando o refinamento exige.

## 6. Forma
- Mudança do tamanho certo; nomes claros; sem código morto nem TODO sem item no tracker.

## Como comentar
- Classifique: **bloqueia** (tem que mudar) ou **sugestão** (pode ficar para depois).
- Aponte arquivo, linha, o problema e a correção esperada; cite a skill ou regra.
- Elogie o que ficou bom quando for exemplo para o time.
