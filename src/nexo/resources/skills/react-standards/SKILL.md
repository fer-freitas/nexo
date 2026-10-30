---
name: react-standards
description: Padrões React/TypeScript da empresa — estrutura, componentes, estado, acessibilidade e chamadas de API. Use ao escrever ou revisar frontend.
---
# Padrões React
- TypeScript estrito; componentes funcionais com hooks; sem `any`.
- Nomenclatura: componentes em PascalCase (`PaymentForm.tsx`), hooks `useXxx`, utilitários camelCase.
- Estrutura por funcionalidade (`features/<nome>/{components,hooks,api}`), não por tipo de arquivo.
- Estado de servidor com biblioteca de cache (ex.: TanStack Query); estado local com `useState`/`useReducer`.
- Chamadas de API isoladas em `api/` com tipos gerados do contrato OpenAPI quando existir.
- Acessibilidade: elementos semânticos, `label` em inputs, navegação por teclado, contraste AA.
- Nunca guarde tokens em `localStorage`; nunca exiba dados pessoais completos sem necessidade.
