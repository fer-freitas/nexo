---
name: contratos-de-api-e-eventos
description: Como definir e versionar contratos de APIs (OpenAPI) e eventos (AsyncAPI e esquema), compatibilidade entre versões e testes de contrato. Use ao criar ou mudar uma API, fila, tópico SNS ou tópico Kafka.
---
# Contratos de API e eventos

Contrato primeiro: o contrato é escrito e aprovado antes do código (tarefa `contract` no backlog, da qual as demais dependem).

## APIs (OpenAPI 3)
- Arquivo em `contracts/<servico>.openapi.yaml`.
- Recursos no plural, substantivos (`/estornos`), verbos HTTP corretos; `POST` que cria recurso aceita `Idempotency-Key`.
- Erros em `application/problem+json` (RFC 9457) com `type`, `title`, `status`, `detail` e código de erro do negócio.
- Paginação por cursor em listas; limite máximo documentado.
- Versionamento no caminho (`/v1/`) só para quebra de compatibilidade.

## Eventos (AsyncAPI + esquema)
- Arquivo em `contracts/<evento>.asyncapi.yaml`; esquema JSON Schema ou Avro no schema registry quando for Kafka.
- Nome no passado e no domínio: `EstornoSolicitado`.
- Envelope padrão:
```json
{
  "id": "uuid do evento",
  "tipo": "EstornoSolicitado",
  "versao": 1,
  "ocorrido_em": "2026-09-28T13:00:00Z",
  "correlation_id": "uuid",
  "origem": "estornos-api",
  "dados": { "estorno_id": "…", "compra_id": "…", "valor": "150.00" }
}
```
- Payload com ids e dados necessários; nada de dado pessoal sem necessidade (`dados-e-lgpd`).
- Valor monetário como string decimal; datas em UTC ISO 8601.

## Compatibilidade
| Mudança | Compatível? |
|---|---|
| Adicionar campo opcional | Sim |
| Adicionar valor em enum | Cuidado: consumidores precisam tolerar desconhecido |
| Remover ou renomear campo | Não: nova versão |
| Mudar tipo ou significado | Não: nova versão |
- Consumidores ignoram campos desconhecidos (tolerant reader).
- Versão nova convive com a antiga até todos os consumidores migrarem; data de desligamento combinada.

## Testes de contrato
- Produtor valida que o que publica segue o esquema; consumidor valida que lê os exemplos do contrato.
- Mudança de contrato roda o teste de contrato dos consumidores conhecidos.

## Checklist
- [ ] Contrato no repositório antes do código
- [ ] Idempotência e erros definidos (API)
- [ ] Envelope, versão e correlação (evento)
- [ ] Mudança classificada como compatível ou nova versão
- [ ] Teste de contrato previsto no refinamento
