---
name: testes-de-caracterizacao
description: Testes de caracterização para código legado — travar o comportamento atual antes de alterar, snapshots, pontos de costura e refatoração segura. Use antes de mexer em código existente sem testes suficientes.
---
# Testes de caracterização

Objetivo: registrar **o que o código faz hoje** (mesmo que estranho) para que a mudança não quebre nada sem querer. Não é para validar se está certo.

## Passos
1. Identifique o trecho que vai mudar e seus pontos de entrada (o Nexo mostra as áreas de baixa cobertura no mapa do código e o impacto no grafo: `nexo graph impact <arquivo>`).
2. Escreva testes que chamam esses pontos com entradas representativas e **assertam a saída atual** (valor, estado gravado, evento, exceção).
3. Para saídas grandes, use snapshot (Verify) revisado por uma pessoa.
4. Rode: todos verdes com o código atual.
5. Faça a mudança; teste de caracterização que quebrar indica mudança de comportamento: é intencional (atualize com aprovação) ou é regressão (corrija).

## Pontos de costura (quando o código não é testável)
- Extrair interface para dependência externa e injetar (sem mudar a lógica).
- Extrair método para isolar o cálculo.
- `TimeProvider` no lugar de `DateTime.Now`.
- Faça a menor mudança possível para testar; refatore depois, com os testes verdes.

## Regras
- Comportamento esquisito é registrado com comentário `// comportamento atual: …` e vira pergunta ao tech lead, não correção silenciosa.
- Caracterização não substitui os testes de aceite da história nova.
- O portão do Nexo para legado vale só no que mudou (baseline).

## Checklist
- [ ] Pontos de entrada do trecho alterado cobertos
- [ ] Testes verdes antes da mudança
- [ ] Mudanças de comportamento aprovadas explicitamente
