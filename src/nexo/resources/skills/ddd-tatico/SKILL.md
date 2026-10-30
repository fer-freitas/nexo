---
name: ddd-tatico
description: DDD tático em .NET — agregados, entidades, value objects, eventos de domínio, repositórios, serviços de domínio e limites de contexto. Use ao modelar ou alterar regras de negócio no projeto Domain.
---
# DDD tático

## Blocos
| Bloco | O que é | Regra |
|---|---|---|
| **Value object** | Valor sem identidade (`Dinheiro`, `Cpf`, `PeriodoEstorno`) | Imutável (`record`), valida no construtor, igualdade por valor |
| **Entidade** | Tem identidade e ciclo de vida (`Estorno`) | Muda estado só por métodos com nome do negócio |
| **Agregado** | Grupo consistente com uma raiz (`Compra` com seus `Estornos`) | Transação altera um agregado; referência a outros só por id |
| **Evento de domínio** | Algo que aconteceu (`EstornoSolicitado`) | Criado pela raiz; publicado após persistir (outbox) |
| **Repositório** | Coleção de agregados | Um por raiz; interface no Domain/Application, implementação na Infrastructure |
| **Serviço de domínio** | Regra que não cabe em uma entidade | Sem estado, sem I/O |
| **Caso de uso (Application)** | Orquestra: carrega, chama o domínio, salva, publica | Sem regra de negócio |

## Exemplo
```csharp
public sealed record Dinheiro
{
    public decimal Valor { get; }
    public Dinheiro(decimal valor)
    {
        if (valor <= 0) throw new ValorInvalidoException(valor);
        Valor = decimal.Round(valor, 2);
    }
}

public sealed class Compra
{
    private readonly List<Estorno> _estornos = new();
    public CompraId Id { get; }
    public Dinheiro Total { get; }
    public DateTimeOffset AprovadaEm { get; }
    public IReadOnlyCollection<Estorno> Estornos => _estornos;

    public Estorno SolicitarEstorno(Dinheiro valor, DateTimeOffset agora)
    {
        if (agora - AprovadaEm > TimeSpan.FromDays(90)) throw new EstornoForaDoPrazoException(Id);
        if (valor.Valor > SaldoEstornavel()) throw new EstornoAcimaDoSaldoException(Id, valor);
        var estorno = Estorno.Criar(Id, valor, agora);
        _estornos.Add(estorno);
        AdicionarEvento(new EstornoSolicitado(estorno.Id, Id, valor.Valor, agora));
        return estorno;
    }
}
```

## Regras
- Regra de negócio mora no domínio; controller, handler e consumidor só orquestram.
- Nada de setters públicos em entidades; nada de entidade anêmica com a regra no serviço de aplicação.
- Invariantes do agregado garantidas em todo método que muda estado.
- Agregados pequenos; se a transação precisa de dois agregados, use evento e consistência eventual.
- Nomes do glossário (`dominio-do-produto`); linguagem ubíqua no código e nos testes.
- Domain sem dependência de EF Core, AWS SDK, HTTP ou logging.
- Testes unitários do domínio sem mocks (é código puro).

## Checklist
- [ ] Regra nova está no domínio, com teste unitário
- [ ] Value objects para conceitos com validação
- [ ] Uma transação, um agregado
- [ ] Evento de domínio para o que outros contextos precisam saber
