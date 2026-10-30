---
name: angular-standards
description: Padrões Angular da empresa — componentes standalone, signals, serviços, RxJS, formulários, acessibilidade e segurança no front. Use ao escrever ou revisar código Angular.
---
# Padrões Angular

Regras garantidas pelo portão `standards` (resources/standards/angular.yaml) estão marcadas com **[portão]**.

## Estrutura
- Por funcionalidade: `src/app/features/<nome>/{domain,data,ui}`; compartilhado em `src/app/shared/`.
- `domain/`: tipos e regras em TypeScript puro; não importa `@angular/*`, `rxjs`, store nem `data`/`ui` **[portão]**.
- `data/`: serviços de acesso a API (`HttpClient`), mapeando DTO ↔ modelo.
- Componentes falam com serviços/fachadas; `HttpClient` direto no componente gera aviso **[portão]**.

## Componentes
- Standalone, `ChangeDetectionStrategy.OnPush`, `inject()` para dependências.
- Estado local com **signals** (`signal`, `computed`); entradas com `input()`/`output()`.
- Template sem lógica de negócio: use `computed` ou pipes puros. Controle de fluxo novo (`@if`, `@for` com `track`).
- Componente de apresentação (sem serviço) separado do de página (orquestra dados).

## RxJS
- Assinatura gerenciada: `async` pipe, `toSignal` ou `takeUntilDestroyed()`; nada de `subscribe` sem fim.
- Operadores de achatamento conscientes (`switchMap` para busca, `concatMap` para ordem, `exhaustMap` para envio).

## Tipos e erros
- `strict` ligado; sem `any` **[portão]** (use `unknown` + validação).
- `catch` vazio é proibido **[portão]**; erros HTTP tratados no interceptor ou no serviço, com mensagem ao usuário.
- Sem `console.log` em produção **[portão]**.

## Formulários e acessibilidade
- Reactive Forms tipados; validação no front **e** no backend.
- Elementos semânticos, `label` associado, navegação por teclado, contraste AA; `aria-*` só quando o HTML não basta.

## Segurança
- Nunca `bypassSecurityTrust*` sem revisão; nada de `innerHTML` com conteúdo do usuário.
- Token fora do `localStorage` (cookie `HttpOnly` pelo backend); dados pessoais mascarados na tela.

## Qualidade (pipeline do Nexo)
- `ng lint` (quando o projeto tem angular-eslint), `tsc -p tsconfig.app.json`, `ng test` (Vitest), `ng build`.
- Pacote novo só depois do `check_dependency` (`npm`) e da aprovação humana; o agente não roda `ng add` **[política]**.
