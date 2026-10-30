# Backlog

O que ainda precisa ser desenvolvido. Para incluir um item, acrescente uma linha em **Novos**. Quando ele entrar em desenvolvimento, passe para **Próximos**, e apague a linha quando estiver feito: o histórico fica no Git.

O que depende de prova real, e não de código, está em [provas-reais.md](provas-reais.md). O que já foi provado está em [estado.md](estado.md).

## Novos

| Item | Por que importa |
|---|---|
| Contextualizar o projeto, trocando o nome "produto" por "projeto" | A IA e as pessoas precisam saber do que se trata, com o nome que todos usam |
| Informar de qual squad a pessoa participa | Custo, métricas e flags dependem disso |
| Enriquecer o contexto do projeto | O contexto precisa crescer com o uso, sem depender de editar arquivo à mão |
| Inicialização rápida da CLI, para teste manual | Testar sem preparar repositório, time nem ferramenta de IA |
| Inicialização rápida do site, para validar | Ver o portal com dados, sem AWS |
| Documentação no layout do Itaú Unibanco | É o padrão visual de quem vai ler |
| CLI no mesmo design | Mesma identidade no terminal |
| Desenho da arquitetura | Para quem vai operar e revisar |
| Desenho funcional | Para quem vai usar e aprovar |
| Features | Saber o que existe e em que estado |
| Como vender o projeto para um executivo | Conseguir apoio e prioridade |
| Documentação para os usuários | Cada papel usar sem pedir ajuda |
| Setup do Nexo instala tudo que é necessário | Hoje a falta de uma ferramenta só aparece depois |

## Próximos

| Item | Por que importa | Origem |
|---|---|---|
| SBOM e cobertura de domínio em `scripts/validate.py` | A spec pede, e hoje não rodam | §20, §27 |
| Instalar `pip-audit` e `semgrep` sem travar dependências do projeto | Hoje ficam como "não verificado" | §27 |
| Efeito da flag por escopo no portal | A tela só mostra o total de máquinas da empresa, porque a tabela de dispositivos não guarda o squad | §26.2, §27 |
| ACL e schema registry na descoberta do Kafka | Hoje só lê grupos e tópicos de um JSON exportado | §23.5, §27 |
| Tirar as dependências do central do binário da CLI | O binário tem 319 MB e leva `pyarrow`, que só o central usa | Build em macOS |

## Depois do MVP

Conexão com o SSO da empresa, `eval` e harness, `brain.graph`, `deps`, `legacy`, `program`, `notify`, `autonomy`, `isolation`, `docsite`, insights e skills do Nexo expostas dentro das próprias CLIs de IA. Detalhes no §19 da [spec](../SPEC.md).
