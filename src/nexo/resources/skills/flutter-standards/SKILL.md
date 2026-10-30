---
name: flutter-standards
description: Padrões Flutter/Dart da empresa — camadas (domain/data/presentation), estado, widgets, assincronia, erros, acessibilidade e segurança no app. Use ao escrever ou revisar código Flutter ou Dart.
---
# Padrões Flutter

Regras garantidas pelo portão `standards` (resources/standards/flutter.yaml) estão marcadas com **[portão]**.

## Estrutura e camadas
- Por funcionalidade: `lib/features/<nome>/{domain,data,presentation}`; código comum em `lib/core/`.
- `domain/`: entidades, regras e interfaces de repositório em **Dart puro**. Não importa `package:flutter/`, `http`,
  `dio`, `shared_preferences`, Firebase nem `dart:io`, e não importa `data/` nem `presentation/` **[portão]**.
- `data/`: implementa as interfaces do domínio (API, banco local), converte DTO ↔ entidade.
- `presentation/`: widgets e o estado da tela; fala com casos de uso ou repositórios pela interface.
- Injeção pelo construtor (ou pelo provedor de estado adotado no projeto); nada de singletons globais com estado.

## Estado
- Siga o gerenciador já adotado no projeto (Riverpod, Bloc/Cubit ou Provider); não misture dois na mesma feature.
- Estado imutável (`final`, `copyWith`, `freezed` se o projeto usa); a tela reage ao estado, não o altera direto.
- Estados explícitos de carregamento, sucesso, vazio e erro.

## Widgets
- Widget pequeno e com um papel; `build` longo vira subwidgets (classes, não métodos `_buildX`) **[portão: função > 60 linhas]**.
- `const` sempre que possível; `Key` em listas que reordenam.
- Nada de lógica de negócio, chamada HTTP ou formatação de regra dentro do `build`.
- Depois de `await`, confira `if (!mounted) return;` antes de `setState` ou de usar o `context` **[portão]**.

## Assincronia e erros
- `async`/`await` com tipos (`Future<Resultado>`); nunca `Future` solto sem tratamento (`unawaited` só intencional).
- Falhas do domínio como tipos (`sealed class` de resultado/erro), não `String`.
- `catch` vazio é proibido **[portão]**; capture o tipo específico (`on SocketException`), registre e trate.
- Sem `print` em produção: use o logger do projeto ou `debugPrint` **[portão]**; nunca registre dado pessoal ou token.

## Segurança no app
- Tokens e segredos só em armazenamento seguro (`flutter_secure_storage`), nunca em `SharedPreferences` nem no código.
- Chaves de API não ficam no binário; o que precisa ser secreto fica no backend.
- HTTPS sempre; valide certificados (sem `badCertificateCallback` liberando tudo).

## Acessibilidade e textos
- `Semantics`/`semanticLabel` em ícones e imagens com significado; alvos de toque ≥ 48dp; contraste AA.
- Textos visíveis pela internacionalização do projeto (`intl`/ARB), não fixos no widget.

## Qualidade (pipeline do Nexo)
- `dart format` sem diferença, `flutter analyze` sem warning (infos não reprovam), `flutter test --coverage`.
- Dependência nova só depois do `check_dependency` (ecossistema `pub`) e da aprovação humana; o agente não roda
  `flutter pub add` **[política]**.
- Código gerado (`*.g.dart`, `*.freezed.dart`) é regerado com `dart run build_runner build`, nunca editado à mão.

## Checklist
- [ ] Domínio em Dart puro, sem Flutter/HTTP
- [ ] Estados de carregamento, vazio e erro tratados
- [ ] `mounted` conferido depois de `await`
- [ ] Sem `print`, sem `catch` vazio, sem segredo no app
- [ ] Semântica e textos internacionalizados
