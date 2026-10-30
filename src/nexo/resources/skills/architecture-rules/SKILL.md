---
name: architecture-rules
description: Regras de dependência entre camadas por stack. Use ao criar arquivos novos ou revisar estrutura.
---
# Regras de arquitetura
- Domínio não depende de infraestrutura, frameworks web ou banco.
- Aplicação orquestra casos de uso; infraestrutura implementa portas.
- Controllers/handlers finos; lógica de negócio fora da camada de transporte.
- .NET: `Api → Application → Domain`; `Infrastructure → Application`. Python: `adapters → app → domain`. React: componentes não chamam `fetch` direto — use `api/`.
