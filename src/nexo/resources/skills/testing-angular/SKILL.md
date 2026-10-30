---
name: testing-angular
description: Testes em Angular no Nexo — Vitest pelo ng test (Angular 21+), TestBed, testes de componente pelo DOM, serviços com HttpTestingController e cobertura. Use ao escrever ou revisar testes Angular.
---
# Testes Angular

E2E: Playwright (`playwright.config.*` liga a etapa). Integração com backend: `testes-de-integracao`.

## Organização
- Arquivo `<nome>.spec.ts` ao lado do código; o portão avisa quando regra nova do domínio não tem `.spec.ts` **[portão]**.
- Angular 21+ usa **Vitest** pelo `ng test` (builder `@angular/build:unit-test`); projetos antigos com Karma/Jest
  continuam pelo `ng test`.
- Testes de aceite com o id do critério no nome (`it('AC-1 recusa pedido vazio', ...)`) ou `// @AC-1` no arquivo.

## Arquitetura
- O isolamento do domínio (sem `@angular`, RxJS, HTTP) já é conferido pelo portão `standards` **[portão]**.
  Não escreva teste que lê arquivos do projeto (`node:fs`, `process.cwd()`): o `ng test` roda em ambiente de
  navegador (jsdom) sem os tipos do Node, e o teste nem compila.

## Escrita
- `describe` por unidade, `it` por comportamento; Arrange / Act / Assert.
- Domínio (`domain/`): TypeScript puro, **sem TestBed** e sem mocks.
- Componente: `TestBed.configureTestingModule({ imports: [Componente] })`, `fixture.detectChanges()` / `await
  fixture.whenStable()`; asserte pelo DOM (texto, papel, `data-testid`) e não por propriedades privadas.
- Signals: altere a entrada com `fixture.componentRef.setInput(...)` e confira o DOM.

## Dublês
- HTTP: `provideHttpClient()` + `provideHttpClientTesting()` e `HttpTestingController` (`expectOne`, `flush`, `verify`).
- Serviços: `{ provide: Servico, useValue: { metodo: vi.fn() } }`; nunca chame API real.
- Tempo: `vi.useFakeTimers()` (Vitest) ou `fakeAsync`/`tick` (zone); nada de `Date.now()` no código testado.

## Cobertura
- Com `@vitest/coverage-v8` no projeto, o Nexo roda `ng test --coverage` (Cobertura XML) e mede a **cobertura das
  linhas alteradas** com o mínimo do `refinamento.yaml` **[portão]**. Sem o pacote, a cobertura não é medida:
  peça a inclusão pelo `check_dependency`.

## Checklist
- [ ] Domínio sem TestBed
- [ ] Componente testado pelo DOM
- [ ] HTTP com HttpTestingController, sem API real
- [ ] Critérios com `AC-n` no nome ou `// @AC-n`
- [ ] Cobertura das linhas alteradas ≥ mínimo
