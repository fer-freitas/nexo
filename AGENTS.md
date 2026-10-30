# Regras para quem trabalha neste repositório

Vale para pessoas e agentes de IA. O `CLAUDE.md` aponta para este arquivo.

## Antes de dizer que terminou

- Rode `.venv/bin/python scripts/validate.py` e confira o código de saída. Procurar `FAILED` na saída não basta, porque erro de coleta não aparece assim.
- "Instalado", "configurado" e "funcionando" são afirmações diferentes. Nenhum estado em `docs/estado.md` muda sem comando, versão e resultado.
- Simulador não prova ferramenta real. `guard_proven` e `verified` só viram `true` com evidência.

## Arquitetura

- Módulo não importa módulo. Use `kernel.services`, declarado em `services:` no manifesto, ou um evento. `tests/architecture` e o import-linter recusam o contrário.
- O kernel e o central não conhecem módulos nem a CLI.
- Módulo novo entra só com a pasta e o `module.yaml`. Todo comando precisa de flag, e o kit `nexo.testing.ModuleContract` confere. Passo a passo em [docs/criar-modulo.md](docs/criar-modulo.md).
- Nenhuma flag desliga o piso (`modules.FLOOR`).
- Limite de 700 linhas por arquivo.

## Windows é o destino

- Sem `shell=True`, sem depender de `sh` e sem caminho fixo como `/tmp`. Use `tempfile` e `pathlib`.
- `read_text`, `write_text` e `subprocess` com texto declaram `encoding`. Arquivo que entra em hash é gravado com `newline="\n"`.
- `tests/architecture/test_portability.py` confere o que dá para provar fora do Windows. A prova real é a fase MF.

## Nomes reservados

Use `.nexo/nexo.yaml`, pastas de usuário do sistema, variáveis `NX_*`, keyring `nexo-cli` e `.brain/`. Nunca `.nexo/config.yaml`, `~/.nexo`, `NEXO_*` ou o keyring `nexo`.

## GitHub Actions

Nada deste projeto cria, altera ou depende de workflow. A política do agente proíbe escrever em `.github/workflows/**`.

## Regras de edição

- `ruff --fix` com regras selecionadas apaga `# noqa` válidos, porque só enxerga as regras selecionadas. Rode com a configuração completa e `--fixable`.
- Não use expressão regular global em código. Use AST ou troca exata.
- Depois de mudar comando em manifesto, gere de novo `tests/snapshots/help.*.txt`.
- A documentação se edita em `docs/site/src/` e `docs/site/estilo.css`. Depois, rode `python scripts/build_docs.py`.
