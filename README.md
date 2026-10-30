# Nexo

CLI para PM, QA, tech lead e dev que conduz o trabalho do pedido até o PR. As skills e os fluxos vêm de packs de mercado, e quem decide o que passa são portões determinísticos.

**Estado:** em construção, antes da primeira liberação. O que já foi provado e o que falta está em [docs/estado.md](docs/estado.md). Novos itens entram em [docs/backlog.md](docs/backlog.md). A especificação completa está em [SPEC.md](SPEC.md).

## Documentação

Abra [docs/site/index.html](docs/site/index.html) no navegador: como funciona, arquitetura, features, a apresentação para executivos e o guia do usuário.

## Experimentar em dois minutos

```bash
nexo setup       # confere e instala o que falta, perguntando antes
nexo demo cli    # repositório de exemplo com agente simulado, sem gastar tokens
nexo demo site   # portal em 127.0.0.1, com um login por perfil
```

## Começar

Pré-requisitos: Python 3.12 ou mais recente e Git. Para rodar tarefas de IA, pelo menos uma destas ferramentas instalada e autenticada: Claude Code, Devin CLI ou GitHub Copilot CLI.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"    # Windows: .venv\Scripts\pip install -e ".[dev]"
.venv/bin/nexo setup
```

Em cada repositório de código, uma vez:

```bash
nexo init
nexo team link <time> --repo <endereço do repositório nexo-time>
nexo squad set <seu-squad>
nexo context init                    # poucas perguntas sobre o projeto e o repositório
nexo lineage agent install           # opcional: mantém a linhagem atualizada nesta máquina
```

Quem entra num time já existente usa só `nexo join <time> --repo <endereço>`, sem precisar saber Git.

## O dia a dia por papel

Digitar só o papel mostra o que está com você agora.

| Papel | Comandos mais usados |
|---|---|
| **PM** | `nexo pm` · `pm idea "<texto>"` · `pm decide IDEA-n build` · `pm split` · `pm refine F-n` · `pm answer` · `pm approve F-n` · `pm accept US-n` |
| **Tech lead** | `nexo tl` · `tl setup` · `tl analyze F-n` · `tl plan F-n` · `tl assign F-n` · `tl gate US-n` · `tl pass US-n` |
| **QA** | `nexo qa` · `qa plan US-n` · `qa write US-n` · `qa run` · `qa trace US-n` · `qa bug US-n "<descrição>"` · `qa pass US-n` |
| **Dev** | `nexo dev` · `dev take US-n` · `dev refine US-n` · `dev run US-n` · `dev problem US-n` · `dev resume US-n` |
| **Todos** | `nexo board` · `nexo brief <id>` · `nexo problem <id>` · `nexo try "<pedido>"` · `nexo help <comando>` |

Todo comando aceita `--json` e `--help`. Para testar sem criar demanda, use `nexo try` e `nexo test`: não abrem PR nem aprovam nada.

## Como o projeto está organizado

| Pasta | Conteúdo |
|---|---|
| `src/nexo/kernel/` | Piso de segurança, flags, serviços, eventos e tudo que não depende de módulo |
| `src/nexo/modules/` | Um módulo por pasta, com `module.yaml`. Módulo não importa módulo: fala por serviço ou evento |
| `src/nexo/cli/` | Montagem da CLI e comandos internos para os quais os atalhos de papel apontam |
| `src/nexo/central/` | Coletor na AWS e portal: features, dashboard, insights, board e acessos |
| `src/nexo/resources/` | Política, receitas, rotas, skills e os packs de terceiros travados por hash |
| `deploy/central/` | Dockerfile e Terraform do coletor |
| `scripts/` | `validate.py`, o portão local de cada versão, e `build_binary.py` |
| `docs/` | Estado do projeto, roteiro das provas reais e como criar um módulo |

## Desenvolvimento

O projeto não conta com GitHub Actions. A validação roda na máquina:

```bash
.venv/bin/python scripts/validate.py          # lint, camadas, tipos e todos os testes
.venv/bin/python scripts/validate.py --fast   # versão rápida
.venv/bin/python scripts/validate.py --install-hook   # pre-push opcional
```

`NX_FORBIDDEN_TERMS`, com termos separados por vírgula, faz a validação recusar esses termos no repositório. A lista nunca fica aqui.

Para gerar o binário no sistema de destino: `pip install -e ".[build]"` e `python scripts/build_binary.py`.

Licenças de terceiros estão em [NOTICE](NOTICE).
