---
name: testing-java
description: Testes em Java no Nexo — JUnit 5, AssertJ, Mockito, testes de fatia do Spring, Testcontainers, ArchUnit e cobertura JaCoCo das linhas alteradas. Use ao escrever ou revisar testes Java.
---
# Testes Java

Integração: `testes-de-integracao`. Carga: `testes-de-carga`. Mutação: `testes-de-mutacao` (PIT).

## Organização
- `src/test/java` espelha `src/main/java`; classe `<Classe>Test` (unitário) e `<Classe>IT` (integração, Failsafe).
- O portão avisa quando regra nova do domínio não tem `<Classe>Test` **[portão]**.
- Testes de aceite com o id do critério: `@DisplayName("AC-1 recusa pedido vazio")` ou método `ac1_recusaPedidoVazio`
  e comentário `// @AC-1` no arquivo. O Nexo lê os relatórios do Surefire/Gradle.

## Escrita
- JUnit 5; Arrange / Act / Assert separados; um comportamento por teste.
- AssertJ para asserções legíveis (`assertThat(pedido.total()).isEqualByComparingTo("10.00")`).
- `@ParameterizedTest` (`@CsvSource`, `@MethodSource`) para limites e variações.
- Asserte o resultado observável (retorno, estado, evento publicado), não detalhes internos.

## Dublês
- Domínio: **sem mocks** e sem Spring (`new` direto, rápido).
- Aplicação: Mockito nas portas (`@ExtendWith(MockitoExtension.class)`); `verify` só quando a interação é o comportamento.
- Não use `@SpringBootTest` para teste unitário. Fatias: `@WebMvcTest` (controller), `@DataJpaTest` (repositório).
- Banco, fila e serviços externos reais na integração com Testcontainers; nunca H2 fingindo ser o banco de produção.

## Tempo e dados
- `Clock` injetado (`Clock.fixed(...)` no teste); nada de `LocalDateTime.now()` no código testado.
- Builders de teste (`PedidoBuilder.umPedido().comItens(2)`); dados fictícios.

## Cobertura e arquitetura
- JaCoCo; o portão mede a **cobertura das linhas alteradas** com o mínimo do `refinamento.yaml` **[portão]**.
- O isolamento do domínio (sem Spring, JPA, HTTP) já é conferido pelo portão `standards` **[portão]**. ArchUnit só
  se o projeto já o tem; não adicione a dependência só para isso.

## Checklist
- [ ] Domínio testado sem Spring e sem mocks
- [ ] Critérios com `AC-n` no nome ou `// @AC-n`
- [ ] Fatia do Spring certa (sem `@SpringBootTest` à toa)
- [ ] `Clock` fixo, dados fictícios
- [ ] Cobertura das linhas alteradas ≥ mínimo
