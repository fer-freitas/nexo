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
| SBOM e cobertura de domínio em `scripts/validate.py` | A spec pede, e hoje não rodam | §20, §27 |
| Instalar `pip-audit` e `semgrep` sem travar dependências do projeto | Hoje ficam como "não verificado" | §27 |
| Efeito da flag por escopo no portal | A tela só mostra o total de máquinas da empresa, porque a tabela de dispositivos não guarda o squad | §26.2, §27 |
| ACL e schema registry na descoberta do Kafka | Hoje só lê grupos e tópicos de um JSON exportado | §23.5, §27 |
| Confirmar a identidade visual com o time de marca | A paleta é aproximada, e o logo e a fonte da empresa dependem de liberação | Layout da documentação |

## Depois do MVP

Já antecipados, sem prova real: `deps`, `legacy`, `program`, `change`, `docsite`, `notify`, `autonomy`, `isolation`, `insights`, `eval`, a travessia do `brain.graph` e os comandos dentro das CLIs de IA.

| Ainda falta | Situação |
|---|---|
| Conexão com o SSO da empresa | Fica para depois, por decisão. A porta `AuthProvider` está pronta |
| Casos de harness do toolkit no `eval` | Handoff desatualizado, parada e retomada, bloqueio parcial e revisão somente leitura |
| Teste automático de `nexo docs portal` | Veio da base sem teste |
| `brain.graph`: seleção de contexto por vizinhança e grafo único de código e cérebro | Hoje só há a travessia |
