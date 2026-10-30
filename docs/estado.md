# Estado do projeto

Atualizado em 2026-10-30, na versão 0.1.0. Este documento separa o que foi provado de verdade do que só passou com simulador.

**Resumo:** o código das entregas M0 a M8 está feito e passa na validação local em macOS. **Nada foi provado no Windows, com contas reais da empresa ou na AWS. O Nexo ainda não pode ser liberado às pessoas.**

## O que foi verificado

| Verificação | Resultado | Como |
|---|---|---|
| Lint, formatação e tipos estritos | Passam | `scripts/validate.py`, em macOS |
| Testes automáticos | 1.119 testes passam, com ferramentas simuladas | `scripts/validate.py`, em macOS |
| Camadas: módulo não importa módulo | Zero dependências diretas | import-linter e `tests/architecture` |
| Kit de contrato de módulo | Todos os módulos passam | `tests/architecture/test_module_contract.py` |
| Portabilidade que dá para provar fora do Windows | Passa | `tests/architecture/test_portability.py` |
| Claude Code real, em projeto descartável | Passou, sem PR e sem revisão por outro fornecedor | [Evidência](../evidence/p0/2026-10-30-claude-selftest.md) |
| Binário `onedir` | Gerado e executado em macOS, 200 MB, com o portal de demonstração no ar | `scripts/build_binary.py` e `dist/nexo/nexo demo site` |
| `nexo demo cli` | O roteiro impresso é executado comando a comando | `tests/integration/test_demo.py` |

## O que não foi verificado

| Item | Situação | Quem destrava |
|---|---|---|
| **Módulos antecipados do pós-MVP** | `deps`, `legacy`, `program`, `change` e `docsite` vieram da base. Os quatro primeiros têm testes com simulador, e `docsite` tem só um teste de geração. Nenhum rodou em projeto real | Uso real |
| **Comandos dentro das CLIs** | `nexo pack expose` gera os arquivos, mas não foi conferido se Claude e Copilot os reconhecem | Abrir a ferramenta |
| **Identidade visual** | Só a paleta aproximada, porque o manual de marca não é público. Sem logo nem fonte da empresa | Time de marca |
| **`nexo setup` instalando de verdade** | Só simulado. Os identificadores de pacote do `winget` não foram conferidos, e a TI pode bloquear | Prova na máquina da empresa |
| **Aparência no Windows** | Cores do terminal e páginas conferidas só em macOS | Prova MF |
| **Windows** | Nunca rodou: agendador, Credential Manager, binário, proxy e TLS, caminhos longos | Máquina da empresa (Q-02) |
| **Claude em história real** | Fluxo completo em projeto descartável, com portão humano. Faltam PR no GitHub e revisão por outro fornecedor. [Evidência](../evidence/p0/2026-10-30-claude-selftest.md) | `nexo selftest --agent claude --github <dono>/nexo-selftest` |
| **Devin e Copilot em história real** | Não rodaram. O Devin está instalado aqui, o Copilot não | `nexo selftest`, que consome créditos |
| **Guarda do Devin** | O arquivo `.devin/hooks.v1.json` é gerado, mas o bloqueio não foi provado. O formato da resposta do hook também não | Prova 9a |
| **Guarda do Copilot** | Sem hook, roda só com `--deny-tool` | Prova 9a |
| **GitHub Issues real** | Só simulador | Prova M5 |
| **3 MCPs reais** | Só com servidor de teste local | Prova M7 |
| **AWS** | O Terraform nunca foi aplicado nem validado: a ferramenta não está instalada aqui. O adaptador de KMS só rodou contra simulador | Conta da empresa (Q-21) |
| **Custo contra a fatura** | Não conferido | FinOps, com um mês de dados |
| **Scanner de linhagem em .NET real** | Só com amostras escritas para os testes. A taxa de acerto é desconhecida | Prova M8 |
| **Descoberta na AWS e no Kafka** | Só simulador, e a de Kafka lê um JSON exportado por alguém | Q-12 |
| **Skills nas CLIs** | No Claude e no Copilot são instaladas; no Devin entram pelo prompt. A descoberta real não foi provada | Prova M4 |
| **Teste de uso por papel** | Não feito | Uma pessoa de cada papel |
| **pip-audit, semgrep, SBOM** | As duas primeiras rodam em `validate.py` se instaladas, mas não estão nesta máquina. SBOM e cobertura de domínio não estão no script. O gitleaks já roda e passa | Instalar antes da versão |

O roteiro está em [provas-reais.md](provas-reais.md).

## Decisão com efeito no uso

**Devin e Copilot não recebem dados `confidential` ou `restricted` enquanto a guarda não for provada**, como manda a spec. Como `confidential` é a classe padrão, hoje só o Claude é escolhido por padrão, e a troca automática quando a cota acaba só cai em Devin ou Copilot para tarefas `internal` ou `public`. Com só o Claude disponível, a revisão por outro fornecedor também não acontece, e toda história passa por portão humano. O teste real confirmou isso. Isso muda sozinho quando `guard_proven` virar `true` no contrato da ferramenta, com evidência.

## Critérios do MVP

Nenhum pode ser marcado como cumprido sem as provas acima. Os que dependem só de código têm teste automático com simulador:

| Critério | Teste automático | Prova real |
|---|---|---|
| PM vai de uma frase ao requisito aprovado | Sim | Falta |
| QA vê critério sem teste, registra bug e verifica | Sim | Falta |
| TL entende o portão reprovado com `nexo tl gate` | Sim | Falta o teste de uso |
| Dev roda em outra ferramenta quando a cota acaba | Sim | Falta |
| Demanda chega à pessoa certa em dois computadores | Sim, em clones locais | Falta |
| Só o dev indicado roda sem aviso | Sim | Falta |
| Problema no meio refaz só o afetado | Sim | Falta |
| `nexo try` não abre PR nem aprova | Sim | Falta |
| `nexo board` com 2 projetos | Sim | Falta |
| Módulo novo entra sem editar arquivos centrais | Sim | Não precisa |
| Nenhuma flag desliga o piso | Sim | Não precisa |
| Teste de uso | Não existe | Falta |
