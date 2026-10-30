---
name: spec-writing
description: Como escrever especificações testáveis no Nexo — objetivo, escopo, Gherkin estruturado, contratos, NFRs, classificação de dados e estratégia de testes. Use ao planejar ou refinar features.
---
# Escrita de especificação
- Objetivo = resultado de negócio mensurável; problema = dor atual observável.
- Escopo e fora de escopo explícitos; tudo que não está no escopo está fora.
- Critério de aceite = comportamento observável: Dado (contexto) / Quando (ação) / Então (resultado verificável). Um comportamento por cenário; inclua casos de erro e limites.
- Contratos antes da implementação: OpenAPI 3.1 para HTTP, JSON Schema para payloads, AsyncAPI para eventos.
- NFRs com números (ex.: p95 < 300 ms com 50 req/s; disponibilidade 99,9%).
- Classificação de dados (`data_class`) é do que vai para a IA ao construir (spec, código, exemplos), não dos dados que o sistema trata em produção: sem dado real nos insumos → confidential; dados reais sensíveis nos insumos → restricted. O tratamento de dado pessoal ou de cliente em produção vira risco (personal_data) e requisito de privacidade no design.
- Estratégia de testes cobre unit, integration, contract, e2e, load, architecture, lint e security ("n/a: motivo" quando não se aplica).
- Dúvida bloqueante = sem a resposta, a implementação provavelmente estará errada.
