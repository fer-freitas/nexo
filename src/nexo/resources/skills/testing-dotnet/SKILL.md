---
name: testing-dotnet
description: Testes unitários e de arquitetura em .NET no Nexo — xUnit, padrão AAA, nomes com id de critério, dublês, tempo e dados, cobertura das linhas alteradas e NetArchTest. Use ao escrever ou revisar testes C#.
---
# Testes .NET (unitários e de arquitetura)

Integração: `testes-de-integracao`. Carga: `testes-de-carga`. Mutação: `testes-de-mutacao`.

## Organização
- xUnit; projetos `<Servico>.UnitTests`, `<Servico>.IntegrationTests`, `<Servico>.ArchitectureTests`.
- Uma classe de teste por classe testada; pastas espelham o `src`.
- Projeto de testes em pasta **irmã** do projeto testado (`tests/<Servico>.UnitTests`), nunca dentro dele, e uma
  solução na raiz incluindo os dois (`dotnet new sln`, `dotnet sln add`, `dotnet add <Testes> reference <Projeto>`).
  Se o projeto principal estiver na raiz do repositório, exclua a pasta de testes dele
  (`<Compile Remove="tests/**" />` e `<Content Remove="tests/**" />`): sem isso o build copia artefatos dos testes.
- Testes de aceite com o id do critério: método `AC1_<Comportamento>` e comentário `// @AC-1` (o Nexo rastreia critério → teste).

## Escrita
- Arrange / Act / Assert, separados por linha em branco; um comportamento por teste.
- Nome: `Metodo_Condicao_ResultadoEsperado` ou frase do negócio (`Estorno_depois_de_90_dias_e_recusado`).
- `[Theory]` + `[InlineData]`/`[MemberData]` para limites e variações.
- FluentAssertions (ou Shouldly, conforme padrão da empresa) para mensagens claras.
- Asserte o resultado observável (retorno, estado, evento gerado), não detalhes internos.

## Dublês
- Domínio: **sem mocks** (código puro).
- Aplicação: dublês das portas (NSubstitute/Moq); verificar interações só quando a interação é o comportamento (ex.: evento publicado).
- Nunca mockar o que você não possui (SDK da AWS, HttpClient): use um adaptador seu e teste o adaptador na integração.

## Tempo, aleatoriedade e dados
- `TimeProvider`/`FakeTimeProvider` para datas; nada de `DateTime.Now` no código testado.
- Builders para objetos de teste (`CompraBuilder.Aprovada().HaDias(30)`); dados fictícios.

## Cobertura
- Coverlet (`XPlat Code Coverage`); o portão mede a **cobertura das linhas alteradas** com o mínimo definido no `refinamento.yaml` **[portão]**.
- Cobertura alta com assert fraco não vale: o teste de mutação mostra isso.

## Arquitetura
- NetArchTest: Domain não referencia Infrastructure/Api; Application não referencia Infrastructure; controllers não acessam repositórios diretamente **[portão]**.

## Checklist
- [ ] Um comportamento por teste, AAA
- [ ] Critérios de aceite com `// @AC-n`
- [ ] Limites e erros cobertos
- [ ] Sem DateTime.Now, sem dados reais
- [ ] Cobertura das linhas alteradas ≥ mínimo
