---
name: testing-flutter
description: Testes em Flutter/Dart no Nexo — unitários do domínio, testes de widget, integração, dublês (mocktail), tempo, golden e cobertura LCOV das linhas alteradas. Use ao escrever ou revisar testes Dart.
---
# Testes Flutter

Integração com backend: `testes-de-integracao`. Mutação: `testes-de-mutacao`.

## Organização
- `test/` espelha `lib/` (`lib/features/pedido/domain/pedido.dart` → `test/features/pedido/domain/pedido_test.dart`);
  sufixo `_test.dart` sempre. O portão avisa quando regra nova do domínio não tem teste **[portão]**.
- `integration_test/` para fluxos no dispositivo/emulador (opcional no pipeline: precisa de device).
- Testes de aceite com o id do critério no nome (`test('AC-1 recusa pedido vazio', ...)`) ou comentário `// @AC-1`
  no arquivo: o Nexo rastreia critério → teste pelo relatório do `flutter test --machine`.

## Arquitetura
- O isolamento do domínio (Dart puro, sem Flutter/HTTP) já é conferido pelo portão `standards` **[portão]**; não
  escreva teste que lê arquivos do projeto para isso.
- Teste de aceite escrito antes do código cita classes e funções que ainda não existem: tudo bem (o analisador só
  roda depois da implementação). Use os nomes do contrato/refinamento para o dev implementar exatamente esses.

## Escrita
- `group` por unidade, `test`/`testWidgets` por comportamento; Arrange / Act / Assert separados.
- Domínio: testes de Dart puro, **sem mocks** e sem `WidgetTester`.
- Widget: `testWidgets` + `pumpWidget` com o mínimo de árvore (`MaterialApp(home: ...)`); `pumpAndSettle` só quando
  há animação; procure por texto, `Key` ou semântica (`find.bySemanticsLabel`), não por tipo interno.
- Asserte o que o usuário vê ou o estado resultante, não chamadas internas.

## Dublês
- `mocktail` (sem geração de código) para as interfaces de repositório/serviço; `registerFallbackValue` para tipos
  próprios usados com `any()`.
- Nunca chame HTTP real: o cliente HTTP fica atrás de uma interface do projeto (ou `MockClient` do `package:http/testing.dart`).
- Provedores de estado: sobrescreva no teste (`ProviderScope(overrides: ...)`, `BlocProvider.value`).

## Tempo e dados
- Relógio injetável (`clock` do pacote `clock` ou uma interface própria); nada de `DateTime.now()` no código testado.
- `fakeAsync` para timers e debounce; dados fictícios, nunca dados reais de cliente.

## Golden (opcional)
- Só para componentes visuais estáveis; atualize com `flutter test --update-goldens` e revise a imagem no PR.

## Cobertura
- `flutter test --coverage` gera LCOV; o portão mede a **cobertura das linhas alteradas** com o mínimo do
  `refinamento.yaml` **[portão]**. Código gerado fica fora da conta.

## Checklist
- [ ] Teste do domínio sem mocks e sem widget
- [ ] Widget testado pelo que o usuário vê
- [ ] Critérios com `AC-n` no nome ou `// @AC-n`
- [ ] Sem HTTP real, sem `DateTime.now()` no código testado
- [ ] Cobertura das linhas alteradas ≥ mínimo
