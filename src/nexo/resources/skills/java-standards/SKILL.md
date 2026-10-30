---
name: java-standards
description: Padrões Java/Spring da empresa — camadas, injeção pelo construtor, records, exceções, logs, validação e persistência. Use ao escrever ou revisar código Java ou Kotlin de backend.
---
# Padrões Java

Regras garantidas pelo portão `standards` (resources/standards/java.yaml) estão marcadas com **[portão]**.

## Estrutura e camadas
- Pacotes por contexto: `<base>.<contexto>.{domain,application,infrastructure,api}`.
- `domain`: entidades, objetos de valor (`record`) e regras em Java puro; não importa Spring, JPA/Jakarta Persistence,
  HTTP nem `infrastructure` **[portão]**. Interfaces de repositório (portas) ficam no domínio.
- `application`: casos de uso orquestrando o domínio; transação aqui (`@Transactional`), não no controller.
- `infrastructure`: JPA, clientes HTTP, mensageria; entidades JPA separadas do modelo de domínio quando o mapeamento
  distorcer a regra.
- `api`: controllers finos, DTOs (`record`) e validação de entrada (`@Valid`, Bean Validation).

## Código
- Injeção **pelo construtor** (campos `final`); `@Autowired` em campo é proibido **[portão]**.
- Imutabilidade: `record` para DTOs e valores; `List.copyOf` ao expor coleções.
- `Optional` só como retorno; nunca como campo ou parâmetro. Sem `null` como regra de negócio.
- Métodos curtos (≤ 40 linhas) e com até 4 parâmetros **[portão]**.
- `BigDecimal` para dinheiro, `java.time` para datas (com `Clock` injetado).

## Exceções e logs
- `catch` vazio é proibido **[portão]**; capture o tipo específico e trate, converta ou propague com contexto.
- Exceções de negócio próprias (`PedidoRecusadoException`), mapeadas para HTTP no `@ControllerAdvice` (Problem Details).
- SLF4J com placeholders (`log.info("pedido {} aprovado", id)`); nada de `System.out` nem `printStackTrace` **[portão]**.
- Nunca registre dado pessoal, token ou payload completo.

## Persistência
- Migrações versionadas (Flyway/Liquibase); nada de `ddl-auto=update` fora do ambiente local.
- Consultas com parâmetros (nunca concatenação); cuidado com N+1 (`fetch join`, `@EntityGraph`).

## Qualidade (pipeline do Nexo)
- Build com o wrapper do projeto (`./mvnw`/`./gradlew`) quando existir; testes com JaCoCo.
- Dependência nova só depois do `check_dependency` (ecossistema `maven`, nome `grupo:artefato`) e da aprovação humana.

## Checklist
- [ ] Domínio sem Spring/JPA
- [ ] Injeção pelo construtor, campos `final`
- [ ] Sem `catch` vazio, sem `System.out`
- [ ] DTOs como `record`, validação na entrada
- [ ] Migração versionada para mudança de esquema
