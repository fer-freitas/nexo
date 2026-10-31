# Roteiro das provas reais

Estas provas não dão para fazer com simulador. Registre cada resultado em `evidence/p0/` ou `evidence/<versão>/`, com comando, versão da ferramenta, data e resultado.

## P0: ambiente

| # | Como provar | Se falhar |
|---|---|---|
| 1 | Criar `nexo-teste`, interno, só com `main`, legível pela organização | Usar S3 ou redesenhar o repositório do time |
| 2 | Dar push nele e conferir que nenhum workflow forçado dispara | Conversar com a plataforma |
| 3 | Criar um branch no padrão da empresa e abrir PR pela API | Ajustar `git.branch_pattern` |
| 5 | `nexo lineage agent install` e um `git pull` com mudança: o hook `post-merge` roda | Linhagem sem o gatilho do hook |
| 9 | `nexo selftest --agent claude`, depois `devin` e `copilot` | Tirar a ferramenta do MVP ou ajustar o leitor |
| 9a | Tentar uma ação proibida no Devin e no Copilot. Só depois disso, `guard_proven: true` no `capabilities.yaml` | A ferramenta continua sem receber dados `confidential` |
| 9b | Subir a imagem de `deploy/central/` pelo padrão de implantação da empresa | Definir com o time de plataforma |

## MF: testes finais no Windows

Sem isso, o Nexo não é liberado às pessoas.

| # | Como provar |
|---|---|
| 4 | `nexo lineage agent install`: a tarefa aparece em `schtasks /query`, sem pedir administrador |
| 6 | `python scripts/build_binary.py` no Windows e rodar `dist\nexo\nexo.exe doctor`, com e sem assinatura. Conferir se o EDR ou o AppLocker bloqueia |
| 7 | `nexo login github`: grava e lê no Credential Manager |
| 8 | `nexo doctor` e uma chamada real à API do GitHub e aos fornecedores, passando pelo proxy e pela inspeção de TLS |
| 10 | `nexo dev run` num repositório .NET real, com caminho acima de 260 caracteres |
| — | Fluxo completo de PM, QA, TL e dev, em duas máquinas, sem tratamento especial |
| — | Leitores de consumo do Copilot e do Devin: `nexo usage` bate com o que a ferramenta mostra |

## Provas de produto

| Entrega | O que precisa acontecer |
|---|---|
| M3 | Uma história real com cada uma das 3 ferramentas, com teste bloqueado, portão, bloqueio de política e revisão por outro fornecedor. Um problema aberto durante `dev run` pausa só a tarefa afetada, e a retomada em outra ferramenta refaz só ela |
| M4 | Skills descobertas de verdade nas 3 CLIs. No Devin, hoje elas entram pelo prompt |
| M5 | 2 projetos reais no mesmo `nexo board`, e GitHub Issues real, sem duplicar ao reenviar |
| M6 | O vault abre no Obsidian, com o grafo sem link quebrado. Editar nele não quebra `brain pull` |
| M7 | Deploy na AWS, máquinas de squads diferentes no mesmo painel, custo batendo com a fatura de um mês, 3 MCPs provados |
| M8 | Repositórios .NET reais, sem workflow, e medição de acertos e erros do scanner com uma lista conferida à mão, antes de ligar `lineage.gate` |
| Uso | Uma pessoa de cada papel executa o fluxo só com a ajuda da CLI |

## Primeira implantação do central

```bash
cd deploy/central/terraform
terraform init && terraform plan        # o Terraform deste repositório nunca foi aplicado
python -m nexo.central.jobs first-admin <pessoa>   # dentro do contêiner; mostra o convite uma vez
```
