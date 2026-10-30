---
name: testing-python
description: Convenções de testes Python no Nexo — pytest, nomes com id de critério, fixtures, integração com Testcontainers. Use ao escrever testes Python.
---
# Testes Python
- pytest; arquivos `tests/test_<assunto>.py`; funções `test_ac_<n>_<comportamento>` para testes de aceite, com comentário `# @AC-<n>`.
- Arrange/Act/Assert explícitos; um comportamento por teste; mensagens de assert claras.
- Fixtures para dados sintéticos; nunca dados pessoais reais.
- Integração com banco/fila via Testcontainers; marque com `@pytest.mark.integration`.
- Arquitetura com import-linter (contratos no pyproject).
