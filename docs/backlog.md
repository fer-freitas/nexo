# Backlog

O que ainda precisa ser desenvolvido. Para incluir um item, acrescente uma linha em **Novos**. Quando ele entrar em desenvolvimento, passe para **Próximos**, e apague a linha quando estiver feito: o histórico fica no Git.

O que depende de prova real, e não de código, está em [provas-reais.md](provas-reais.md). O que já foi provado está em [estado.md](estado.md).

## Novos

| Item | Por que importa |
|---|---|
| | |

## Próximos

| Item | Por que importa | Origem |
|---|---|---|
| Traduzir para inglês o restante das mensagens da CLI | `--lang en` hoje cobre a ajuda, o board e parte das mensagens | Roteiro de testes |
| Unificar `projetos/` e `projects/` no cérebro | O contexto do projeto fica numa pasta e as notas e flags do projeto em outra, com nomes quase iguais | Página do cérebro |
| Confirmar o formato da sigla do projeto | Hoje aceita de 2 a 8 letras ou números, sem ligação com o cadastro oficial da empresa | Contexto do projeto |
| Confirmar a identidade visual com o time de marca | A paleta é aproximada, e o logo e a fonte da empresa dependem de liberação | Layout da documentação |

## Depois do MVP

Já antecipados, sem prova real: `deps`, `legacy`, `program`, `change`, `docsite`, `notify`, `autonomy`, `isolation`, `insights`, `eval`, os casos de harness, o `brain.graph` com seleção de contexto por vizinhança e os comandos dentro das CLIs de IA.

| Ainda falta | Situação |
|---|---|
| Conexão com o SSO da empresa | Fica para depois, por decisão. A porta `AuthProvider` está pronta |
