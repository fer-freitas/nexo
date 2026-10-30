---
name: python-standards
description: Padrões Python da empresa — estrutura, tipagem, lint, logging e erros. Use ao escrever ou revisar código Python.
---
# Padrões Python
- Python 3.12+; layout `src/`; `pyproject.toml`; dependências com `pip` num `.venv` do projeto, versões travadas em `requirements*.txt` com hashes (`pip-compile --generate-hashes`).
- Rode as ferramentas pelo Python do ambiente: `.venv/bin/python -m pytest` (Windows: `.venv\Scripts\python -m pytest`); o Nexo prepara o `.venv` do worktree antes de você começar.
- Tipagem completa (mypy estrito); `ruff` para lint e formatação.
- Nomenclatura PEP 8: `snake_case` funções/variáveis, `PascalCase` classes, `UPPER_CASE` constantes.
- Funções pequenas e puras no domínio; I/O nas bordas (adaptadores).
- Erros: exceções específicas; não use `except Exception` sem relançar ou registrar.
- Logging estruturado; nunca logue segredos ou dados pessoais.
- Datas com timezone (`datetime.now(UTC)`); dinheiro com `Decimal`.
