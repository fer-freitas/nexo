---
name: testing-react
description: Convenções de testes React no Nexo — Vitest, Testing Library, MSW e Playwright. Use ao escrever testes de frontend.
---
# Testes React
- Vitest + Testing Library; arquivos `*.test.tsx` ao lado do componente; `it("AC-1 ...")` para aceite, com comentário `// @AC-1`.
- Consulte elementos por papel/rótulo (acessibilidade), não por classe CSS.
- MSW para simular APIs; nada de chamadas reais de rede.
- E2E com Playwright em `e2e/*.spec.ts`, apenas em `localhost`.
