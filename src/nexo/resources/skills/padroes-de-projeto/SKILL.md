---
name: padroes-de-projeto
description: Catálogo de padrões de projeto usados na empresa com C# — Chain of Responsibility, Strategy, Factory, Decorator, Specification, Outbox, Mediator e outros — quando usar, quando não usar e exemplo. Use ao estruturar regras que variam, pipelines de validação ou integrações.
---
# Padrões de projeto

Use padrão para resolver um problema presente, não um futuro imaginado. Se um `if` resolve e não vai crescer, use o `if`.

## Chain of Responsibility
**Quando:** sequência de validações ou etapas independentes que podem parar o fluxo (ex.: validações de um estorno: prazo, saldo, antifraude, limite diário).
```csharp
public interface IValidacaoEstorno
{
    Task<ResultadoValidacao> ValidarAsync(PedidoEstorno pedido, CancellationToken ct);
}

public sealed class PipelineValidacaoEstorno(IEnumerable<IValidacaoEstorno> etapas)
{
    public async Task<ResultadoValidacao> ExecutarAsync(PedidoEstorno pedido, CancellationToken ct)
    {
        foreach (var etapa in etapas)                       // ordem definida no registro de DI
        {
            var r = await etapa.ValidarAsync(pedido, ct);
            if (!r.Valido) return r;                         // interrompe a cadeia
        }
        return ResultadoValidacao.Ok();
    }
}
// services.AddScoped<IValidacaoEstorno, PrazoValidacao>(); … na ordem desejada
```
**Regras:** cada elo testado isoladamente; ordem explícita e testada; elo não conhece o próximo.
**Não use** quando as etapas dependem umas das outras de forma complexa (use um caso de uso explícito).

## Strategy
**Quando:** algoritmo que varia por tipo (cálculo de tarifa por produto). Registre estratégias por chave; nunca `switch` crescente.

## Factory
**Quando:** criação com regra (escolher a estratégia, montar agregado válido). Métodos `Criar` estáticos no domínio para entidades.

## Decorator
**Quando:** somar comportamento transversal sem mexer na classe (cache, métrica, retry, log). Em .NET, `Scrutor` ou registro manual.

## Specification
**Quando:** regra de negócio reutilizável e combinável (`EstornavelSpecification`), inclusive para consultas.

## Mediator (MediatR ou similar)
**Quando:** desacoplar transporte de casos de uso e aplicar comportamentos de pipeline (validação, transação). **Não use** só para "ter mediator"; um handler chamado direto também é válido.

## Transactional Outbox
**Quando:** gravar no banco e publicar evento sem perder consistência. Grava o evento numa tabela na mesma transação; um publicador envia e marca como enviado. Obrigatório quando o evento não pode se perder.

## Adapter / Anti-corruption layer
**Quando:** integrar sistema externo ou legado: traduz o modelo deles para o nosso na Infrastructure; o domínio nunca vê o modelo externo.

## Checklist
- [ ] O padrão resolve um problema real e presente
- [ ] Nome da classe revela o papel no padrão
- [ ] Cada peça testável isoladamente
