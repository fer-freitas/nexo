# Nexo — Especificação do produto e da arquitetura

| | |
|---|---|
| **Versão** | 2.2 |
| **Data** | 2026-10-30 |
| **Estado do repositório** | M0 a M8 implementadas e validadas com simuladores. Sem prova real: ver [docs/estado.md](docs/estado.md) e o §27. |
| **Referência externa** | `vibe-coding-toolkit` (commit `21b71b6`) |

**Em uma frase:** o Nexo é uma CLI para PM, QA, tech lead e dev que conduz o trabalho do pedido até o PR. Ela usa skills e fluxos conhecidos do mercado, passa tudo pelos portões determinísticos herdados da base e registra cada demanda gerada, em cada projeto.

**Base:** neste documento, é o código interno já existente de onde os módulos são extraídos.

**Convenção de maturidade usada neste documento:**

- 🟢 **Existe e foi testado** na base, com ferramenta real.
- 🟡 **Existe, mas sem verificação real** (só simulador ou contrato não conferido).
- 🔵 **A construir** no Nexo.

---

## 1. Objetivo e limites

### 1.1 Problemas que o Nexo resolve

1. **A base é poderosa e difícil de usar.** São 130 comandos registrados (104 visíveis) organizados por assunto técnico. Um PM ou QA precisa saber o que é `plan package` ou `quality perf`.
2. **A base é difícil de estender.** As flags ficam numa tupla fixa em `domain/config/flags.py` e os comandos numa tabela de 1.030 linhas em `cli/registry.py`. A camada `app/` tem dependências circulares entre todos os contextos. Módulo novo significa mexer em vários pontos centrais.
3. **O conteúdo da base é próprio.** As 63 skills não aproveitam o que o mercado já adotou e mantém.
4. **Não há visão de demandas entre projetos, nem de quem está com o quê.** A base não tem campo de responsável. Cada projeto sincroniza com o seu tracker, e o que a execução gera (bugs, vulnerabilidades, achados de revisão) não forma uma fila consolidada.
5. **O segundo cérebro não serve para navegar.** Ele não tem frontmatter, IDs nem links, então não abre bem no Obsidian nem alimenta um grafo.

### 1.2 Fora do escopo

- Reescrever os portões, a política de segurança ou os adaptadores que já funcionam. Eles são extraídos, não reinventados.
- Criar uma ferramenta de IA nova. O Nexo orquestra as que já existem.
- Instalar Spec Kit, BMAD ou OpenSpec lado a lado. Dois frameworks de spec na mesma mudança criam duas fontes de verdade (§9.4).
- Vender o Nexo, multi-tenant e cobrança. É uma ferramenta interna da empresa.

---

## 2. Decisões

### 2.1 Tomadas por você

| ID | Decisão | Consequência principal |
|---|---|---|
| D-01 | **O Nexo será da empresa** | Ferramenta interna, com código, cérebro e dados na organização. O responsável liberou o Nexo na empresa (§25.1). |
| D-02 | **Extrair a base por módulos em repo novo** | `domain/` e `adapters/` são copiados quase intactos; `app/` e `cli/` são reorganizados em módulos. |
| D-03 | **Toolkit como pack versionado sobre o motor determinístico** | O toolkit não tem código executável. O "core" de conteúdo vem dele e o de execução vem da base. |
| D-04 | **Tracker local + GitHub Issues + visão consolidada** | Registro no Git de cada projeto, sincronização com o GitHub e `nexo board`. **Jira, Linear, Azure Boards e GitLab foram removidos.** |
| D-05 | **Brain com a estrutura Git de hoje, preparado para grafo** | Mantém PR e assinatura. Todo arquivo ganha ID e links; o grafo é um derivado (§13). |
| D-06 | **Um cérebro global + uma pasta por projeto** | O agente só lê `global` + o projeto atual. Projeto sensível pode ter cérebro próprio. |
| D-07 | **Claude Code, Devin e Copilot no MVP, aprovados pela empresa** | Três adaptadores com prova real. Codex saiu do escopo, e a porta `Agent` permite trazê-lo de volta (§11). |
| D-08 | **Coletor central no MVP, confirmado** | É o painel da empresa para FinOps e métricas de squads. Vira um serviço interno (§15). É a decisão que mais aumenta o prazo. |
| D-31 | **Portal web como módulo: gestão de features, dashboard e insights** | Roda junto com o central, e o SSO entra depois sem refazer nada (§26). |
| D-28 | **Os repositórios são da empresa, e nada sai da organização** | Cérebro, repositório do time e serviços ficam na organização. Só fornecedores de IA aprovados (§25.1). |
| D-29 | **Windows é o destino** | Todo o código segue as regras de Windows desde a M0, e a prova real fica para os testes finais (§25.2). |
| D-33 | **Construção inicial na máquina e no repositório privado do responsável** | Migra para a organização antes do primeiro uso com dados da empresa. Até lá, só projetos de teste (§25.1). |
| D-32 | **Central na AWS, operado pelo time do Nexo** | ECS Fargate, RDS PostgreSQL e balanceador interno, com a infraestrutura em Terraform (§15.5). |
| D-30 | **Nada de branch especial nos repositórios de código** | Branches órfãos provavelmente não são permitidos. Demandas, refinamento e linhagem vão para um repositório do time (§14.3). |
| D-16 | **Modo de teste sem demanda** | `nexo try` e `nexo test` não criam demanda (§7.6). |
| D-17 | **Fluxo real de refinamento: funcional → técnico → dev → construção** | Ideia gera features, e features geram histórias. O Nexo sabe com quem cada demanda está (§8). |
| D-18 | **O tech lead define os responsáveis** | `nexo tl assign` indica dev e QA por história (§8.4). |
| D-22 | **Problema na construção pausa só o que foi afetado** | Tarefas não afetadas continuam. Se o impacto for incerto, a história inteira para (§8.10). |
| D-23 | **Só o tech lead libera premissa provisória** | A IA nunca assume. O PR não recebe merge até o dono confirmar (§8.10). |
| D-25 | **Linhagem em nível de componente, entre repositórios e times** | Foco em .NET, Kafka, SQS/SNS, ECS e Lambda. Sem nível de campo (§23). |
| D-26 | **Linhagem automática na máquina de cada pessoa** | Sem máquina dedicada e sem servidor. Usa as credenciais Git de quem está na máquina (§23.2). |
| D-27 | **O Nexo inteiro não conta com GitHub Actions** | Os workflows seguem um padrão predefinido, e não é possível criar ou apagar tarefas. O Nexo só lê o resultado do que já existe (§24). |

### 2.2 Decisões técnicas assumidas (mude se discordar)

| ID | Decisão | Motivo |
|---|---|---|
| D-09 | Python 3.12, Typer, pip com hashes | Continuidade com o código extraído. |
| D-10 | Papel organiza os comandos e valida aprovações, mas **não restringe leitura** | Um QA precisa poder ver o design; só aprovar é restrito. |
| D-11 | **Modo solo** declarado em `team.yaml`, só para testes e projetos de uma pessoa | **O padrão é `team`**, com segregação de funções. A IA nunca aprova (§7.5). |
| D-12 | Demandas no repositório do time (`nexo-<time>`) | Não suja o diff das histórias, não conflita entre worktrees e os portões não o enxergam (§14.3). |
| D-13 | `.nexo/nexo.yaml`, pastas de usuário do sistema, variáveis `NX_*`, cofre `nexo-cli` | Evita nomes reservados (§3). |
| D-14 | Coletor com PostgreSQL em produção; SQLite só para teste local | Vários squads gravando ao mesmo tempo exigem um banco de verdade. |
| D-15 | Interface em pt-BR; conteúdo do pack mantido no idioma original | Traduzir o pack dificultaria acompanhar o upstream. O agente responde no idioma da pessoa. |
| D-19 | Requisitos e design são aprovados por feature; testes e plano, por história | Cada história pode ter QA e dev diferentes (§7.5). |
| D-20 | Refinamento mora no repositório do time até a história ficar pronta | É como a demanda chega à máquina de outra pessoa sem o PM precisar saber Git (§8.8). |
| D-21 | Quem está com a demanda é **calculado**, não digitado | Regras fixas evitam a demanda parada com a pessoa errada (§8.2). |
| D-24 | Cada time publica a linhagem no próprio repositório `nexo-<time>`, e a organização lê todos | Não exige servidor nem acesso ao código de outro time. A permissão de escrita é a do próprio repositório (§23.4). |

---

## 3. Nomes reservados

Estes nomes têm comportamento especial em ferramentas anteriores que podem estar nas máquinas: pastas podem ser renomeadas, e variáveis e credenciais, copiadas. Para evitar isso, o Nexo não os usa.

| Proibido | Risco | Usar |
|---|---|---|
| `.nexo/config.yaml` | A pasta do projeto pode ser renomeada | Marcador `.nexo/nexo.yaml` |
| `~/.nexo` | Tokens e estado podem ser copiados para outro lugar | Pastas de usuário do sistema (§17) |
| Variáveis `NEXO_*` | Podem ser reaproveitadas por outra ferramenta | Prefixo `NX_*` |
| Serviço `nexo` no keyring | Credenciais podem ser migradas | Serviço `nexo-cli` |
| Pasta `.nexo-brain/` | Confusão sobre qual chave assinou o quê | `.brain/manifest.json` |

O teste `tests/architecture/test_reserved_names.py` falha se algum desses nomes aparecer no código do Nexo.

---

## 4. Arquitetura

### 4.1 Visão geral

```text
           PM          QA         Tech lead        Dev
            └───────────┴────────────┴──────────────┘
                              │
                     nexo CLI + console            (§7)
                              │
   ┌──────────────────────────┴───────────────────────────┐
   │ MÓDULOS (ligam e desligam por flag)                  │
   │ planning · execution · quality · delivery · demands  │
   │ knowledge · mcp · usage · finops · central-client    │
   │ packs · agents.claude/copilot/devin · ...            │
   └──────────────────────────┬───────────────────────────┘
                              │ só por portas e eventos
   ┌──────────────────────────┴───────────────────────────┐
   │ KERNEL (piso, nunca desligável)                      │
   │ módulos · flags · config · identidade · política     │
   │ redação · injeção · auditoria · kill switch · portões│
   └──────────────────────────────────────────────────────┘
        │              │               │              │
  CLIs de IA      nexo-brain       trackers    coletor central
  (subprocesso)   (Git assinado)   + SCM       (serviço à parte)
```

**Três regras que tornam isso modular de verdade:**

1. **Módulo não importa outro módulo.** Conversa por portas (`Protocol` no kernel) e eventos. O import-linter garante isso na validação local (§20).
2. **Tudo o que um módulo oferece está no manifesto dele.** Comandos, flags, portões e recursos não ficam em tabelas centrais.
3. **O kernel nunca depende de módulo.** Desligar qualquer módulo não pode desligar um controle de segurança.

### 4.2 Estrutura do repositório

```text
nexo/
├── SPEC.md · README.md · AGENTS.md · CLAUDE.md · NOTICE · CHANGELOG.md
├── pyproject.toml · requirements/
├── src/nexo/
│   ├── kernel/                 # piso: sem rede nem disco no núcleo de regras
│   │   ├── modules/            # carregador, manifesto, grafo de dependências
│   │   ├── flags/              # resolução, ciclo de vida
│   │   ├── config/             # cascata
│   │   ├── ports/              # Tracker, Agent, Scm, Brain, Telemetry...
│   │   ├── events/             # barramento e contrato de eventos
│   │   ├── security/           # política, redação, injeção, ambiente, kill switch
│   │   ├── audit/              # trilha encadeada
│   │   ├── gates/              # registro de portões, decisão, nota de confiança
│   │   └── errors.py           # códigos de saída estáveis
│   ├── modules/
│   │   └── <módulo>/
│   │       ├── module.yaml     # manifesto (§4.3)
│   │       ├── domain/         # regras puras
│   │       ├── adapters/       # mundo externo
│   │       ├── app/            # casos de uso
│   │       ├── cli.py          # comandos
│   │       ├── resources/      # skills, prompts, receitas, templates
│   │       └── tests/
│   ├── store/                  # SQLite local
│   ├── cli/                    # só monta a CLI a partir dos manifestos
│   └── central/                # coletor (só importa kernel), implantado à parte
├── packs/                      # packs de fábrica e lock (§9)
├── deploy/central/             # Dockerfile, compose, exemplos de proxy
├── tests/                      # arquitetura, contrato, integração, e2e, simuladores
└── scripts/                    # bootstrap, lock, validação de entrega
```

### 4.3 Contrato de módulo

**Adicionar um módulo é criar uma pasta com este manifesto**, sem editar nenhum arquivo central.

```yaml
# src/nexo/modules/demands/module.yaml
id: demands
version: 1.0.0
title: Rastreamento de demandas
requires: []                       # módulos obrigatórios
optional: [central-client]         # usa se estiver ligado
ports_used: [Tracker, Telemetry]
ports_provided: [DemandLedger]
flags:
  - key: demands.board
    title: Visão consolidada de demandas
    stage: ga                      # experimental | beta | ga | deprecated
    default: true
  - key: demands.external_sync
    title: Sincronização com a ferramenta de gestão
    stage: beta
    default: false
commands:
  - path: board
    entry: nexo.modules.demands.cli:board
    roles: [pm, qa, tl, dev]
    kind: read                     # read | write | agent | gate
    flag: demands.board
    summary: Demandas de todos os projetos
events:
  emits: [demand.created, demand.transitioned]
  consumes: [gate.decision, bug.created]
gates: []                          # portões que este módulo registra
resources:
  skills: resources/skills
data_class_max: confidential
```

O carregador valida o manifesto com um esquema, recusa dependências circulares e nomes de comando duplicados, e monta a ajuda e o console a partir disso. **Módulos externos** entram pelo grupo de entry points `nexo.modules`, somente se o ID estiver em `org/modules.yaml` do brain.

Todo módulo é testado por um **kit de contrato** (`nexo.testing.ModuleContract`): manifesto válido, comandos com `--json`, comando sem flag declarada falha o teste, e o módulo sobrevive com todos os opcionais desligados.

### 4.4 Catálogo de módulos

LOC é o tamanho aproximado de cada parte na base.

| Módulo | Faz o quê | Na base | LOC aprox. | Estado | MVP |
|---|---|---|---|---|---|
| `kernel` | Piso | `domain/security`, `gates`, `config`, `workflow`; `store` | 3.000 | 🟢 | Sim |
| `setup` | `init`, `doctor`, `login`, `update` | `app/platform/{project,doctor,selfmanage}` | 900 | 🟢 | Sim |
| `planning` | Discovery, refinamento, papéis, DoR, templates | `app/planning`, `domain/planning`, `domain/spec`, `domain/templates` | 5.700 | 🟢 | Sim |
| `execution` | Tarefa, história, feature, roteamento, handoff, worktrees | `app/execution`, `domain/routing`, `adapters/workspace` | 6.800 | 🟢 | Sim |
| `quality` | Receitas, padrões, rastreabilidade, papel QA | `adapters/quality`, `domain/quality` | 1.500 | 🟢 + 🔵 QA | Sim |
| `delivery` | PR, revisão, evidências | `app/delivery` (parte), `adapters/scm` | 1.500 | 🟢 GitHub / 🟡 demais | Sim |
| `demands` | Registro, responsáveis, sincronização, board | `app/integrations/tracker`, `adapters/trackers/{base,github_issues,file_exchange}`, `domain/tracker` | 1.000 + novo | 🟡 GitHub Issues / 🔵 registro e responsáveis | Sim |
| `sandbox` | `nexo try` e `nexo test`, sem demanda | `app/execution/run` e `quality`, por porta | novo | 🔵 | Sim |
| `lineage` | Linhagem de componentes entre repositórios e times (§23) | `domain/planning/product`, `domain/knowledge/portal` | 400 + novo | 🔵 | M8 |
| `knowledge` | Brain, writer, ask, grafo, lições | `app/knowledge`, `adapters/knowledge`, `domain/knowledge`, `domain/learning` | 5.100 | 🟢 + 🔵 lint e grafo | Sim |
| `packs` | Pack de skills, lock, transpilação | `domain/extensions`, `adapters/knowledge/extensions` | 400 + novo | 🟡 / 🔵 | Sim |
| `mcp` | Catálogo, aprovação, conexão | `app/integrations/mcp_catalog`, `resources/mcp` | 300 + novo | 🟡 todos não verificados | Sim |
| `usage` | Coletor local de consumo das CLIs | `adapters/usage`, `app/finops/external_usage` | 800 | 🟢 Claude, Copilot, Devin / 🔵 validar no Windows | Sim |
| `finops` | Custo, preços, orçamento | `app/finops`, `domain/finops` | 1.700 | 🟢 | Sim |
| `central-client` | Telemetria, outbox, flags remotas | `app/platform/telemetry`, `domain/monitoring` | 700 | 🟢 | Sim |
| `central` (serviço) | API de ingestão, fatos, flags remotas | `collector/` (exceto telas) | 5.500 (§15) | 🟡 | Sim |
| `portal` | Site: gestão de features, dashboard, insights e board (§26) | `collector/dashboard`, `metrics/`, `app/web`, `insights/` | 2.000 + novo | 🟡 + 🔵 gestão de features | Sim |
| `agents.*` | Claude, Copilot, Devin | `adapters/agents`, `resources/agents` | 1.350 | ver §11 | Sim |
| `notify` | E-mail, Teams | `adapters/channels/notify` | 200 | 🟡 | Depois |
| `legacy` | Modernização, incidente | `app/planning/{modernize,incident}` | 500 | 🟢 | Depois |
| `deps` | Vulnerabilidades em escala | `app/delivery/deps`, `adapters/dependencies` | 600 | 🟢 | Depois |
| `program` | Feature em vários repositórios | `app/delivery/program` | 200 | 🟢 | Depois |
| `change` | ITSM | `app/delivery/change` | 100 | 🟡 | Depois |
| `isolation` | Agente em contêiner | `app/platform/isolation` | 200 | 🟡 | Depois |
| `eval` | Avaliação de skills, rotas, harness | `app/knowledge/evaluation` + prompts 14 e 15 do toolkit | 300 | 🟢 + 🔵 | Depois |
| `autonomy` | Autonomia L0–L3, merge automático | `domain/workflow/autonomy` | 300 | 🟢 | Depois |
| `docsite` | Docusaurus, portal | `app/knowledge/{docsite,portal}` | 600 | 🟢 | Depois |

---

## 5. Personas

| Papel | O que quer | O que nunca deveria precisar saber |
|---|---|---|
| **PM** | Transformar uma ideia em requisito aprovado, saber o que está parado e quanto custa | Git, worktree, rota de modelo |
| **QA** | Garantir que cada critério tenha teste, rodar validações, registrar bugs e dar o aceite de qualidade | Como o agente implementa |
| **Tech lead** | Analisar, desenhar, definir o pronto, decidir nos portões e proteger a arquitetura | Detalhes de sincronização com o tracker |
| **Dev** | Pegar uma tarefa clara, executar com o agente, resolver falhas e entregar o PR | Fluxo de aprovação do PM |

**Requisito de ferramenta por papel:** a IA do PM e do QA é a mesma configurada na máquina. Se o PM não tiver uma CLI de agente, o `nexo` pode usar a API direta (`adapters/agents/api.py`) com uma chave. O `doctor` explica em linguagem simples o que falta para cada papel.

---

## 6. Feature flags

### 6.1 Precedência

```text
central:projeto → central:time → central:global → cérebro:projeto → cérebro:time → cérebro:global → local (só desligar) → padrão do manifesto
```

Herda da base `domain/config/flags.py` a resolução, as fontes e o TTL de 120 s com último valor conhecido quando offline. **Muda:** além do time (`squad`), entra o nível `projeto`, o catálogo sai do manifesto dos módulos e entra a camada local.

### 6.2 Regras

| Regra | Comportamento |
|---|---|
| **Piso** | Política, redação, auditoria, assinatura do cérebro, identidade, portões duros, kill switch e limite de orçamento **não têm flag**. Um teste falha se um módulo tentar declarar flag para eles. |
| **Local só desliga** | `nexo flags set <chave> off --local` é permitido. Ligar localmente o que o cérebro ou o central desligou não é. |
| **Dependência** | Módulo com `requires` desligado desliga junto. O erro diz o porquê. |
| **Ciclo de vida** | `experimental` (padrão desligado) → `beta` → `ga` → `deprecated` (com `remove_after`). Passar do prazo falha a validação local (§20). |
| **Comando desligado** | Some da ajuda. Se chamado direto, sai com código estável e diz qual flag e qual fonte desligaram. |
| **Falha da fonte** | Central fora do ar: usa o cache, depois o cérebro. Flag desconhecida vinda de fonte remota: ignora e avisa no `doctor`. |
| **Auditoria** | Toda mudança vai para a trilha com quem, de onde e o valor anterior. |

### 6.3 Comandos

```bash
nexo flags list [--module demands] [--stage beta]
nexo flags explain demands.board      # valor, fonte vencedora e as demais fontes
nexo flags set <chave> on|off [--project <slug>|--global] [--local]
nexo module list                      # módulos, versão, estado, dependências
```

---

## 7. CLI por papel

### 7.1 Princípios

1. **O primeiro nível é quem você é:** `nexo pm`, `nexo qa`, `nexo tl`, `nexo dev`.
2. **O verbo é de negócio.** `nexo pm refine`, não `plan feature`.
3. **`nexo <papel>` sozinho responde "o que é comigo agora?"**, usando o inbox da base filtrado.
4. **Todo erro diz o próximo comando.**
5. **Uma operação tem uma só implementação.** Se dois papéis a usam, ambos apontam para o mesmo caso de uso.
6. **Sempre** `--json`, `--dry-run` em escrita e código de saída estável.
7. **Quanto mais perto do portão, mais confirmação:** `read` roda direto; `agent`, `write` e `gate` pedem Enter no console, com custo estimado quando `agent`.

### 7.2 Comandos por papel

Comandos em **negrito** são novos ou mudam de comportamento em relação à base.

#### PM

| Comando | O que faz | Na base |
|---|---|---|
| `nexo pm` | O que espera por mim | `inbox` filtrado |
| **`nexo pm idea "<texto>" [--file]`** | Registra a ideia e gera `IDEA-n`, sem IA | novo |
| `nexo pm discover IDEA-n <insumos>` | Problema, evidências, hipóteses e recomendação (opcional) | `plan discovery` |
| `nexo pm decide IDEA-n build\|drop\|investigate --reason` | Decisão | `plan decide` |
| **`nexo pm split IDEA-n [--single]`** | Propõe as features da ideia; o PM edita e confirma | novo |
| `nexo pm refine F-n [--from <arquivos>]` | Enriquece a spec, gera dúvidas e as histórias em rascunho | `plan`, `plan feature` |
| `nexo pm answer F-n` | Responde as dúvidas até a prontidão chegar a 100% | `plan answer` |
| `nexo pm classify F-n <classe>` | Classe de dados: o que pode ir para a IA | `plan classify` |
| `nexo pm estimate F-n` | Custo e prazo pelo histórico | `cost estimate` |
| `nexo pm approve F-n` / `reject F-n --reason` | Aprova ou reprova a parte **requisitos** | `plan approve --part requisitos` |
| **`nexo pm accept US-n`** | Aceite de negócio depois da entrega | novo |
| **`nexo pm confirm P-n` / `decline P-n --reason`** | Decide uma premissa provisória assumida pelo TL | novo |

#### QA

**Lacuna da base:** `ROLES = ("pm", "tech_lead", "dev")`. O QA não existe como papel; hoje há apenas um agente `qa-author` e os comandos `quality`. O Nexo cria o papel.

| Comando | O que faz | Na base |
|---|---|---|
| `nexo qa` | O que espera por mim | novo |
| **`nexo qa plan US-n`** | Monta a matriz critério → cenário → tipo de teste → dado necessário | novo, usa `testes-de-aceite` |
| **`nexo qa approve US-n` / `reject US-n --reason`** | Aprova a nova parte **testes** da história | novo |
| `nexo qa write US-n` | Gera os testes antes do código e os bloqueia | etapa QA de `run story` |
| `nexo qa run [US-n]` | Pipeline de qualidade da stack | `quality run` |
| `nexo qa trace US-n` | Critério → teste → resultado, com o que falta | `domain/quality/traceability` |
| `nexo qa perf script\|check US-n` | Teste de carga com os limites do refinamento | `quality perf` |
| `nexo qa bug US-n "<descrição>" [--evidence]` | Registra bug como demanda ligada e devolve ao dev | parte de `reject` |
| **`nexo qa verify BUG-n`** | Roda de novo e valida a correção antes de fechar o bug | novo |
| **`nexo qa flaky`** | Testes instáveis e o que foi colocado em quarentena | `quality.flaky` hoje só como evento |

#### Tech lead

| Comando | O que faz | Na base |
|---|---|---|
| `nexo tl` | O que espera por mim | `inbox` filtrado |
| `nexo tl setup` | Prepara o repositório (toolkit + discover + grafo). **Não cria workflow**: lê os comandos do pipeline existente | `init`, `discover` |
| `nexo tl analyze F-n` | Lacunas, riscos e impacto | `tech analyze` |
| `nexo tl return F-n --reason` | Devolve ao PM a lacuna funcional | `tech return` |
| `nexo tl spec F-n` | Requisito técnico com premissas | `tech spec` |
| `nexo tl plan F-n` | Primeira quebra em tarefas e o que roda em paralelo | `plan package`, `plan graph` |
| `nexo tl approve F-n` / `reject F-n --reason` | Aprova ou reprova a parte **design** | `plan approve --part design` |
| **`nexo tl idea "<texto>" --tech`** | Registra melhoria técnica sem impacto funcional; o aceite é do TL | novo |
| **`nexo tl assign <US-n\|F-n> --dev <pessoa> --qa <pessoa> [--queue]`** | Define os responsáveis de cada história | novo |
| **`nexo tl assign F-n --suggest`** | Sugere nomes por dono do código e carga atual, sem IA | novo |
| **`nexo tl reassign US-n --dev <pessoa> --reason`** | Troca o responsável, com motivo na trilha | novo |
| **`nexo tl assume P-n --reason`** | Libera a continuação com premissa provisória; o PR fica retido até o dono decidir | novo |
| `nexo tl ready <US-n\|F-n>` | Definition of Ready | `ready` |
| `nexo tl impact <arquivos>` | Quem depende do que mudou | `graph impact` |
| `nexo tl gate US-n` | Mostra o porquê do portão | `status` |
| `nexo tl pass US-n` / `fail US-n --reason` | Decide o portão humano | `approve`, `reject` |
| `nexo tl review <pr>` | Mesmo pipeline para um PR humano | `review pr` |

#### Dev

| Comando | O que faz | Na base |
|---|---|---|
| `nexo dev` | O que espera por mim | `tasks` |
| **`nexo dev take US-n`** | Assume uma história que está na fila do papel | novo |
| **`nexo dev refine US-n`** | Melhora o refinamento: ajusta as tarefas e propõe mudanças ao PM ou ao TL | novo |
| `nexo dev approve US-n` / `reject US-n --reason` | Aprova ou reprova a parte **plano** da história | `plan approve --part plano` |
| `nexo dev explain <T-n\|US-n>` | O quê, por quê, onde mexer e como validar | `explain` |
| `nexo dev ask "<pergunta>"` | Pergunta sobre o código e o cérebro, com fontes | `ask` |
| `nexo dev run <T-n\|US-n\|F-n> [--parallel N]` | Executa com o agente até o portão | `run task\|story\|feature` |
| **`nexo dev problem US-n`** | Triagem guiada de um problema descoberto na construção (§8.10) | novo |
| `nexo dev resume <id> [--dry-run]` | Retoma depois de cota, dúvida, orçamento ou **mudança na spec**, refazendo só o afetado | `resume`, estendido |
| `nexo dev check US-n` | Está pronta de verdade? | `check` |
| `nexo dev pr [sync]` | Abre o PR com a evidência do portão e reage à revisão e ao resultado do pipeline existente | `pr sync` |
| `nexo dev logs <id>` | Linha do tempo auditável | `logs` |

#### Comuns e administração

```bash
nexo                     # console: próximos passos do seu papel
nexo board [--mine] [--project] [--role] [--state] [--blocked] [--origin]
nexo demand show|list|link|cancel <id>
nexo demand tree <id>    # ideia → features → histórias → tarefas e bugs
nexo demand why <id>     # com quem está, por quê e o que falta
nexo brief <id>          # o que mudou desde a sua última ação nesta demanda
nexo return <id> --to pm|tl|qa|dev --reason "..."    # devolve com a pergunta
nexo suggest <id> --section <seção> "..."            # propõe mudança na seção de outro papel
nexo proposals [--mine] · nexo proposals accept|decline <id>
nexo problem <US-n> · nexo problem reclassify <P-n> --kind <tipo>   # dev, qa e tl têm atalho
nexo meeting start <id> --type funcional|tecnico [--with <pessoas>]
nexo meeting note "..." · nexo meeting import <arquivo> · nexo meeting close
nexo meeting review <id>
nexo try "<pedido>" · nexo try flow "<pedido>" · nexo try list|show|discard|promote
nexo lineage show|upstream|downstream|impact|coverage|scan|publish|discover|export   # §23
nexo test [caminhos] [--stage <etapa>]
nexo join <time>         # entra no time sem precisar saber Git
nexo status [id] · nexo cost [--project --month]
nexo brain status|pull|search|propose|lint|index|sign
nexo mcp search|add|approve|check|test|list|remove
nexo pack list|show|add|update|diff|lock
nexo flags ... · nexo module ...
nexo init · nexo doctor [--role qa] · nexo login <serviço> · nexo role set|show
nexo central up|status|pair|tokens · nexo audit verify|export · nexo update
nexo help [--role qa] [<comando>]
```

### 7.3 Console

Mantém o `session.py` e o `intents.py` da base (entendimento de frases sem IA). O cabeçalho passa a filtrar por papel:

```text
$ nexo
Nexo · loja (python) · papel: QA · 3 demandas com você · R$ 4,32 no mês

Próximos passos
  1. US-1-2 sem cobertura em 2 critérios (AC-3, AC-4)
  2. BUG-1-2-1 aguarda sua verificação
  3. F-2 aguarda aprovação da parte testes

nexo › 1
```

### 7.4 Códigos de saída

| Código | Sentido |
|---|---|
| 0 | Sucesso |
| 1 | Erro inesperado |
| 2 | Uso incorreto |
| 3 | Portão reprovado |
| 4 | Aguardando pessoa |
| 5 | Política negou |
| 6 | Desligado por flag ou kill switch |
| 7 | Integridade falhou (assinatura, trilha) |
| 8 | Sem credencial ou ferramenta |

### 7.5 Papéis e aprovações

```yaml
# team.yaml, no repositório do time (versionado)
mode: team          # team (padrão) | solo
members:
  - { id: gh:ana,  roles: [tl] }
  - { id: gh:bia,  roles: [pm] }
  - { id: gh:caio, roles: [qa] }
  - { id: gh:dani, roles: [dev] }
```

| Regra | `team` | `solo` |
|---|---|---|
| Aprovar exige ter o papel | Sim | Sim |
| Autor não aprova o próprio portão | Sim (`check_segregation`) | Permitido, com `sod_waived: true` na trilha e aviso |
| **A IA aprova** | **Nunca** | **Nunca** |
| Portões duros | Sim | **Sim, iguais** |
| Parte aprovada muda depois | Volta a `desatualizada` | Igual |

O modo solo muda só quem pode assinar, nunca o que precisa passar. Trocar de `solo` para `team` é mudança versionada e auditada.

**O pacote passa a ter quatro partes, em dois níveis:**

| Parte | Quem aprova | Nível | Quando |
|---|---|---|---|
| `requisitos` | PM | Feature | Fim do refinamento funcional |
| `design` | TL | Feature | Fim do refinamento técnico |
| `testes` | QA da história | História | Plano de teste pronto |
| `plano` | Dev da história | História | Refinamento do dev pronto |

**Em `team`, dev e QA de uma história precisam ser pessoas diferentes.** `tl assign` recusa a combinação. Em `solo`, é permitido e fica registrado.

### 7.6 Testar sem demanda

Dois comandos que **não criam demanda, não aparecem no board e não vão para o GitHub Issues**. Servem para experimentar uma skill, um agente ou um pedido, e para rodar testes no código atual.

```bash
nexo test                              # pipeline de qualidade no estado atual
nexo test src/pagamentos --stage lint  # só uma parte
nexo try "cria um endpoint de saúde"   # agente real em área descartável
nexo try "..." --agent devin --skill testing-python
nexo try flow "exportar relatório" --fake   # fluxo inteiro simulado, sem custo
nexo try list · nexo try show TRY-3 · nexo try discard TRY-3
nexo try promote TRY-3                 # o experimento vira IDEA-n, com o diff anexado
```

| Tema | `nexo test` | `nexo try` |
|---|---|---|
| Onde roda | Na pasta atual | Em worktree descartável a partir do commit atual |
| Usa IA | Não | Sim (sem custo com `--fake`) |
| Altera o código | Depende da receita; o que estiver nela | Não toca no seu branch |
| Portões | Mostra o resultado, sem decidir nada | Rodam, só como informação |
| Teto de custo | — | `sandbox.try_max_brl`, padrão R$ 5 por execução |
| Prazo | — | Apagado depois de 7 dias |
| Papel | Qualquer um | Qualquer um |

**O que continua valendo, porque é piso:** política de ferramentas, redação, detector de injeção, ambiente mínimo, classe de dados do projeto, kill switch e auditoria.

**O que é bloqueado em `nexo try`:** `git push`, abertura de PR, aprovação e escrita no cérebro. Isso impede que o modo de teste vire um atalho para fugir do fluxo. **Para o resultado ir adiante, só com `promote`**, que cria a ideia e segue o caminho normal, com todos os portões. O diff anexado serve de insumo, não de entrega.

**Custo:** é medido e aparece em `nexo cost` numa linha própria (`experimentos`), para não sumir nem distorcer o custo das demandas.

**Flag:** `sandbox.try`, ligada por padrão. `nexo test` faz o mesmo que `nexo qa run` sem ID.

---

## 8. Fluxo de ponta a ponta

**O que a base não tem e o Nexo cria:** a base não tem entidade de ideia nem campo de responsável, e só materializa as histórias depois da última aprovação (`app/planning/roleflow.py`). O fluxo abaixo muda isso. Reaproveita a sessão conjunta, os motivos de sessão obrigatória (`JointPolicy`), a transcrição de áudio e vídeo (`adapters/workspace/ingest.py`) e a aprovação com hash, que detecta alteração depois de aprovado.

### 8.1 Da ideia à tarefa

```text
IDEA-1   Parcelamento no checkout                       pm idea
├── F-1  Simulador de parcelas                          pm split
│   ├── US-1-1  Ver as parcelas no carrinho             pm refine
│   │   ├── T-1-1-1  Endpoint de simulação              tl plan / dev refine
│   │   └── T-1-1-2  Componente de parcelas
│   └── US-1-2  Regra de arredondamento
│       └── BUG-1-2-1  Centavos errados em 3 parcelas   gerado pelo portão
└── F-2  Parcelamento com juros
```

| Nível | Nasce quando | Quem confirma | O ID muda depois? |
|---|---|---|---|
| `IDEA-n` | `pm idea`, sem IA | PM | Nunca |
| `F-n` | `pm split`: a IA propõe, o PM edita e confirma. Ideia pequena usa `--single` e gera uma só | PM | Nunca |
| `US-n-m` | `pm refine` gera em **rascunho**, com critérios de aceite | PM, ao aprovar `requisitos` | Nunca |
| `T-n-m-k` | `tl plan` faz a primeira quebra e o dev melhora | Dev, ao aprovar `plano` | Nunca. Tarefa removida fica como cancelada |
| `BUG-…` etc. | Execução, QA ou revisão | Ver §14.1 | Nunca |

Uma ideia pode ser descartada (`pm decide IDEA-1 drop`) ou gerar mais features depois (`pm split IDEA-1 --add`). `nexo demand tree IDEA-1` mostra a árvore inteira, com estado, responsável e custo acumulado.

### 8.2 Com quem está a demanda

Cada demanda tem uma **etapa**, e a etapa diz com quem ela está. Ninguém "passa" a demanda à mão: **quando o critério de saída é cumprido, ela vai sozinha para a próxima pessoa**.

| # | Etapa | Nível | Está com | Sai quando | Vai para |
|---|---|---|---|---|---|
| 1 | Ideia | Ideia | PM autor | `pm decide build` + `pm split` | Etapa 2 de cada feature |
| 2 | Refinamento funcional | Feature | PM | Prontidão em 100% + `pm approve` | Etapa 3 |
| 3 | Refinamento técnico | Feature | TL | `tl approve` + `tl assign` em todas as histórias | Etapas 4 e 5, **ao mesmo tempo** |
| 4 | Plano de teste | História | QA indicado | Todo critério com cenário + `qa approve` | Etapa 6, junto com a 5 |
| 5 | Refinamento do dev | História | Dev indicado | Tarefas validadas + `dev approve` | Etapa 6, junto com a 4 |
| 6 | Pronto | História | Automático | DoR passou | Etapa 7. Se falhar, vai ao dono do item que falta |
| 7 | Construção | História | Dev indicado | `dev run` chega ao portão | Etapa 8 |
| 8 | Validação | História | QA indicado | Qualidade verde + rastreabilidade completa | Etapa 9 |
| 9 | Portão | História | TL | `tl pass` | Etapa 10. `tl fail` gera bug e volta à 7 |
| 10 | PR | História | Dev indicado | PR com merge | Etapa 11 |
| 11 | Aceite | História | PM | `pm accept` | Concluída |

**Como é calculado.** A projeção gera este bloco em cada demanda, a partir dos eventos. Ele nunca é editado à mão:

```yaml
stage: dev_refinement
holders:
  - { role: qa,  who: gh:caio, action: "Aprovar plano de teste", since: 2026-11-03T14:10 }
  - { role: dev, who: gh:dani, action: "Melhorar o refinamento", since: 2026-11-03T14:10 }
responsible: { pm: gh:bia, tl: gh:ana, qa: gh:caio, dev: gh:dani }
blocked_by: []
```

Assim uma demanda nunca fica sem dono: se nenhum critério explica a posição dela, `demand verify` falha. `nexo demand why US-1-2` mostra o motivo em uma frase.

### 8.3 Como cada papel enriquece

Cada papel é dono de uma parte do refinamento. **Cada um edita livremente o que é seu e propõe mudanças no que é dos outros.**

| Seção | Dono | Conteúdo |
|---|---|---|
| Funcional | PM | Problema, regras de negócio, critérios de aceite, fora de escopo |
| Técnica | TL | Design, critérios técnicos, premissas, riscos, contratos |
| Testes | QA | Cenários, dados de teste, tipos de teste, não funcionais a verificar |
| Implementação | Dev | Tarefas, arquivos, ordem, estimativa técnica |

**Quando alguém muda algo que é de outro**, a mudança vira uma **proposta** para o dono. Ela não entra no texto até ser aceita:

```bash
nexo dev refine US-1-2
#  ✓ Tarefas atualizadas: 3 → 4 (são suas)
#  → Proposta P-7 para o TL: trocar a fila por chamada direta
#  → Proposta P-8 para o PM: critério AC-3 não cobre parcela zero (bloqueante)

nexo proposals accept P-7     # o TL aceita; o texto muda e a aprovação do design é refeita
```

| Situação | O que acontece |
|---|---|
| Dev ajusta as próprias tarefas | Muda direto e vale ao aprovar `plano` |
| Proposta muda critério de aceite ou critério técnico | **Bloqueia a história** até o dono decidir. A demanda aparece também com ele |
| Proposta é só sugestão | Não bloqueia; fica na lista do dono |
| Dono recusa | O motivo volta para quem propôs |
| Aceitar muda uma seção já aprovada | **Só essa aprovação** volta a `desatualizada`. As outras continuam valendo |
| Mudança funcional afeta os testes | QA é avisado. O plano de teste precisa ser conferido de novo |

Cada seção tem um hash próprio, o que é uma extensão da aprovação com hash da base. É isso que permite invalidar só o que mudou.

**A IA ajuda, mas não aprova.** Cada comando de enriquecimento usa a skill do papel e escreve na seção daquele papel. Se ela encontrar um problema em outra seção, ele vira proposta, como faria uma pessoa.

### 8.4 Como o tech lead define os responsáveis

```bash
$ nexo tl assign F-1 --suggest
US-1-1  dev: gh:dani (dono de checkout/, 2 histórias em andamento)   qa: gh:caio
US-1-2  dev: gh:edu  (dono de pricing/, 0 em andamento)              qa: gh:caio

$ nexo tl assign US-1-1 --dev gh:dani --qa gh:caio
$ nexo tl assign US-1-2 --dev gh:edu  --qa gh:caio
```

| Regra | Comportamento |
|---|---|
| **Sugestão sem IA** | Vem dos donos do código e do histórico do Git (`codemap`), mais a carga atual no board. O TL decide |
| **Quem pode indicar** | Quem tem o papel `tl` |
| **Quem pode ser indicado** | Só quem tem o papel em `team.yaml`. Fora disso, o comando recusa e diz por quê |
| **Sem nome definido** | `--queue` deixa na fila do papel, e o primeiro que rodar `dev take US-1-1` assume |
| **Obrigatoriedade** | A etapa 3 só termina com todas as histórias indicadas ou na fila |
| **Trocar** | `tl reassign` exige motivo. A pessoa antiga e a nova são avisadas, e o trabalho em andamento fica guardado no checkpoint |
| **Quem constrói** | `dev run` só roda para o dev indicado. Outro dev precisa de `dev take --force`, que avisa o TL e fica na trilha |
| **Quem é o TL e o PM** | PM: quem criou a ideia. TL: o `tl` padrão do projeto em `team.yaml`, ou `pm approve F-1 --tl gh:ana` |
| **Modo solo** | Indicação automática para você. Nenhum comando pede nome |

### 8.5 Como a pessoa sabe que está com ela

| Onde | Como |
|---|---|
| **`nexo` ou `nexo <papel>`** | É a primeira coisa que aparece: o que está comigo, ordenado pelo que está parado há mais tempo |
| **`nexo board --mine`** | A mesma lista em tabela, com todos os projetos |
| **GitHub** | O Nexo atribui a issue à pessoa e troca a etiqueta (`nexo:refinamento-tecnico`). O próprio GitHub avisa por e-mail ou celular, **sem o módulo de notificação** |
| **Painel central** | Página "Comigo", para quem prefere não usar o terminal |
| **E-mail ou Teams** | Módulo `notify` |

Ao abrir uma demanda que acabou de chegar, a pessoa vê um **resumo da passagem**, sem precisar ler tudo:

```text
$ nexo brief US-1-2
Chegou para você (QA) há 2h, enviada por gh:ana (TL).

O que foi decidido
  • Arredondamento vai para a última parcela (reunião funcional, 03/11)
  • Sem fila: chamada direta (P-7, aceita pelo TL)

O que esperam de você
  Plano de teste para AC-1, AC-2, AC-3

Em aberto
  P-8: AC-3 não cobre parcela zero (bloqueante, com o PM)

Próximo comando: nexo qa plan US-1-2
```

O resumo vem dos eventos e dos hashes, sem IA, então sai na hora e sem custo.

**Devolução:** `nexo return US-1-2 --to pm --reason "..."` devolve a demanda com a pergunta. Fica registrada e entra no histórico da demanda como retrabalho de refinamento.

**Demanda parada:** `demands.stale_after`, padrão de 2 dias úteis, destaca o item no board e no console de quem indicou.

### 8.6 Reuniões

A reunião não é uma etapa nova. É uma das formas de preencher uma etapa, porque o trabalho também pode ser feito de forma assíncrona. `JointPolicy` diz quando ela é obrigatória: área sensível, dados restritos, feature grande.

```bash
nexo meeting start F-1 --type funcional --with gh:ana,gh:caio,gh:dani
nexo meeting note "Arredondamento vai para a última parcela"   # durante a reunião
nexo meeting import gravacao.mp4                               # ou transcrição, ata, PDF
nexo meeting close        # a IA separa decisões, critérios, dúvidas e pendências
nexo meeting review M-3   # cada dono aceita ou recusa o que é da sua seção
```

| Regra | Motivo |
|---|---|
| **Nada entra direto na spec** | O que a IA extrai vira proposta para o dono da seção. Ela pode ter entendido errado |
| **Encerrar não aprova** | A aprovação continua sendo um comando separado de cada papel |
| **Dúvidas abertas viram perguntas** | Entram na lista do `pm answer`, para não se perderem |
| **Presença é registrada** | Fica na sessão, e a aprovação aponta para ela (`Approval.session` já existe) |
| **Gravação é dado sensível** | Passa pela redação; só o texto redigido fica salvo. O arquivo original não vai para o Git. A classe de dados decide se a transcrição pode ir para o fornecedor de IA, e a padrão é local (`faster-whisper`) |
| **Papéis obrigatórios variam** | Funcional: PM + TL. Técnica: TL + um dev. Configurável. A base exigia os três papéis em toda sessão |

### 8.7 Um dia de trabalho

**Segunda, 9h: a ideia chega.** A PM registra e decide.

```bash
nexo pm idea "Parcelamento no checkout" --file pedido-diretoria.pdf   # IDEA-1
nexo pm decide IDEA-1 build --reason "Abandono de carrinho em 22%"
nexo pm split IDEA-1                                                  # F-1 e F-2
nexo pm refine F-1                                                    # 2 histórias em rascunho, 3 dúvidas
```

**Segunda, 14h: refinamento funcional.** Com PM, TL, QA e dev na sala.

```bash
nexo meeting start F-1 --type funcional --with gh:ana,gh:caio,gh:dani
nexo meeting note "Arredondamento vai para a última parcela"
nexo meeting close
nexo meeting review M-1    # a PM aceita 4 itens e recusa 1
nexo pm answer F-1         # responde a dúvida que sobrou: prontidão em 100%
nexo pm approve F-1        # F-1 vai para a TL. Ela é avisada pelo GitHub
```

**Terça, 10h: refinamento técnico.** A TL prepara a parte dela e conduz a reunião.

```bash
nexo tl                       # F-1 está com você há 20h
nexo brief F-1
nexo tl analyze F-1           # a IA aponta 1 lacuna funcional
nexo return F-1 --to pm --reason "E se o cartão recusar a 2ª parcela?"
# A PM responde. A aprovação de requisitos é refeita e a demanda volta para a TL.
nexo tl spec F-1 && nexo tl plan F-1
nexo meeting start F-1 --type tecnico --with gh:dani,gh:edu
nexo meeting close && nexo meeting review M-2
nexo tl approve F-1
nexo tl assign US-1-1 --dev gh:dani --qa gh:caio
nexo tl assign US-1-2 --dev gh:edu  --qa gh:caio
# As duas histórias vão para o QA e para o dev, ao mesmo tempo.
```

**Terça, 15h: o dev melhora o refinamento e o QA faz o plano.** Em paralelo.

```bash
# Dev
nexo dev                      # US-1-2 chegou para você
nexo brief US-1-2
nexo dev refine US-1-2        # 1 tarefa nova; P-7 para a TL; P-8 bloqueante para a PM
# A TL aceita P-7 e reaprova o design. A PM aceita P-8 e reaprova os requisitos.
nexo dev approve US-1-2

# QA
nexo qa plan US-1-2           # avisado de que AC-3 mudou, confere de novo
nexo qa approve US-1-2        # DoR passa sozinho: US-1-2 está pronta
```

**Quarta: construção e validação.**

```bash
nexo dev run US-1-2           # testes primeiro, código, qualidade, revisão por outro fornecedor
# A qualidade passa: a demanda vai para o QA
nexo qa trace US-1-2          # todo critério com teste
nexo qa bug US-1-2 "Centavos errados em 3 parcelas" --evidence print.png
# BUG-1-2-1 volta para o dev, ligado à história
nexo dev run BUG-1-2-1
nexo qa verify BUG-1-2-1
```

**Quinta: portão, PR e aceite.**

```bash
nexo tl gate US-1-2 && nexo tl pass US-1-2
nexo dev pr                   # PR com o trailer Nexo-Demand
nexo pm accept US-1-2
nexo demand tree IDEA-1       # o que falta, quanto custou, quanto retrabalho teve
```

**Em qualquer momento:** alguém quer testar uma ideia solta, sem criar nada: `nexo try "..."`.

### 8.8 Como a demanda chega à máquina de outra pessoa

A base guarda o refinamento na pasta do projeto, o que só funciona se alguém fizer commit e push. Isso não serve para um PM.

**No Nexo, tudo o que vem antes da construção fica no repositório do time, `nexo-<time>` (§14.3):** ideias, specs, seções, propostas, reuniões e eventos. **Todo comando de papel sincroniza sozinho:** faz `fetch` antes de mostrar e `push` depois de gravar, numa cópia gerenciada pelo Nexo. A pessoa nunca vê Git.

```text
Antes da construção              Na construção                  Depois
nexo-<time>              ──►     branch da história       ──►   PR para main
(editável pelos papéis)          .nexo/specs/US-1-2.lock.yaml   com a spec congelada junto
```

**O PM e o QA não precisam clonar nenhum repositório de código:** basta o do time.

| Tema | Regra |
|---|---|
| **Entrar no time** | `nexo join <time>` clona o repositório do time. O PM só precisa de `nexo login github` |
| **Nome do branch** | Segue o padrão da empresa, em `git.branch_pattern`. O Nexo não impõe nome |
| **Edição simultânea** | Cada seção é um arquivo, e só o dono edita. Se duas pessoas gravarem ao mesmo tempo, o `push` falha, o Nexo refaz sobre a versão nova e, se houver conflito real, avisa |
| **Sem internet** | Fica pendente na fila e envia depois. O console avisa que ela não saiu da máquina |
| **Spec congelada** | Quando a história fica pronta, `.lock.yaml` guarda as seções e os hashes aprovados no branch da história. **O portão confere que o código foi feito contra o que foi aprovado** |
| **Mudança depois de congelar** | Nova proposta aprovada gera outra versão do lock. `dev run` avisa e o portão recusa a versão antiga (§8.10) |

**Limitação:** em `tracking.mode: home`, não há compartilhamento entre pessoas. Esse modo só serve para quem trabalha sozinho.

### 8.9 Atalhos

As etapas pulam conforme a origem. **Nenhum atalho pula portão duro.**

| Origem | Começa em | Motivo |
|---|---|---|
| Ideia ou feature nova | Etapa 1 ou 2 | Fluxo completo |
| Bug de portão ou de QA | Etapa 7 | Já tem história, critério e evidência |
| Bug de produção | Etapa 3 (TL faz a triagem) | Precisa de um dono e de um teste novo |
| Vulnerabilidade | Etapa 3 | Idem |
| Achado da IA (`triage`) | Fora do fluxo, até o TL aceitar | Evita ruído |
| Melhoria técnica sem impacto funcional | Etapa 3, sem PM | `tl idea --tech`. O aceite vira do TL |
| História pequena (`--track fast`) | Etapa 2, sem reunião | Só se `JointPolicy` não exigir. As aprovações continuam |

### 8.10 Problema descoberto durante a construção

**O problema pode surgir no meio do desenvolvimento. O fluxo não recomeça:** a documentação é corrigida pelo dono da seção, e a IA retoma do ponto onde parou.

**O que muda em relação à base:** hoje, uma pergunta do agente leva a **história inteira** de volta ao refinamento (`app/execution/story/clarification.py`). Ao retomar, ela volta à execução sem reaprovar o plano se os critérios continuarem na spec, e revê o plano se eles tiverem sumido. O Nexo mantém essa retomada, mas pausa só o que foi afetado e sabe exatamente o que refazer.

#### Como o problema entra

| Quem descobre | Como | O que acontece |
|---|---|---|
| **O agente**, durante `dev run` | Ferramenta `request_clarification`, que ganha o campo `kind` e a evidência | Ele para aquela tarefa. Nunca decide sozinho e nunca muda a seção de outro papel |
| **O dev** | `nexo dev problem US-n` | Triagem guiada, abaixo |
| **QA ou TL** | `nexo qa problem US-n` ou `nexo tl problem US-n` | A mesma triagem |

A classificação do agente é uma sugestão. O dev pode corrigi-la com `nexo problem reclassify P-n`. O **destino sai de uma tabela fixa**, não da IA:

| O que está errado | `kind` | Vai para | Efeito |
|---|---|---|---|
| Tarefa errada ou faltando | `task` | Dev | Muda direto, sem proposta |
| Design ou premissa técnica | `technical` | TL | Proposta bloqueante nas tarefas afetadas |
| Critério de aceite ou regra de negócio | `functional` | PM | Proposta bloqueante nas tarefas afetadas |
| Teste errado | `test` | QA | Proposta bloqueante. A IA não altera teste bloqueado |
| Problema fora do escopo | `other` | Nova demanda ligada à história | Não trava. Se a origem for a IA, nasce em `triage` |

#### Triagem guiada

```text
$ nexo dev problem US-1-2
O que você encontrou?
  1. A tarefa está errada ou faltando
  2. A solução técnica não funciona
  3. A regra de negócio está errada ou incompleta
  4. Um teste está errado
  5. Outro problema, fora desta história
› 2
Descreva: A API de pagamento não aceita parcela com centavos
Evidência (opcional, Enter para pular): logs/pagamento-422.txt

Isso muda a seção técnica, então vai para gh:ana (TL).
Afetadas: T-1-2-3 e T-1-2-4, e AC-3 precisa de nova conferência
Continuam: T-1-2-5 (sem relação)
Prontas e mantidas: T-1-2-1 e T-1-2-2
Custo até aqui: R$ 3,20
Confirmar? [Enter]
```

Para uso por script: `nexo dev problem US-1-2 --kind technical --text "..." --evidence <arquivo> --json`.

#### Impacto

Calculado sem IA, em `demands.domain.impact`:

| Sinal | Como é usado |
|---|---|
| **Critérios** | A tarefa declara `ac_refs` 🔵 (hoje só a história tem). Mudou o critério, a tarefa é afetada |
| **Seção técnica** | A tarefa declara `section_refs` 🔵. Mudou o trecho, a tarefa é afetada |
| **Arquivos** | `files_hint` da tarefa (já existe) e arquivos alterados no checkpoint. Sobrepôs, é afetada |
| **Testes** | Rastreabilidade critério → teste. Os testes ligados ao que mudou voltam ao QA |

**Na dúvida, para tudo.** Tarefa sem referência ou mudança que o cálculo não consegue situar pausa a história inteira. Pausar demais só atrasa, enquanto continuar por engano gera retrabalho sem ninguém perceber.

#### Enquanto espera

- **Tarefas não afetadas continuam rodando,** cada uma na própria worktree. Decisão D-22.
- **As afetadas param** no próximo checkpoint. O que foi feito fica salvo e não é descartado.
- **A demanda aparece com as duas pessoas:** o dono da decisão, como ação, e o dev, como aguardando (`holders` e `blocked_by`).
- **A etapa continua sendo Construção.** Só vai para refinamento, como na base, quando a história inteira para.
- **Sem resposta:** vale `demands.stale_after` (§8.5). O TL é avisado e pode usar a premissa provisória.

#### Premissa provisória

Quando há prazo e o dono não responde, **só o tech lead** pode liberar a continuação. Decisão D-23.

```bash
nexo tl assume P-9 --reason "PM de férias até quinta; arredondar na última parcela"
nexo pm confirm P-9     # depois, o dono confirma
nexo pm decline P-9 --reason "..."
```

| Regra | Comportamento |
|---|---|
| **Quem pode** | Só o papel `tl`. **A IA nunca assume**, e o dev também não |
| **O que muda** | A seção recebe o trecho marcado como `provisório`, e a spec congelada fica `provisional: true` |
| **Portão** 🔵 | **PR não recebe merge enquanto houver premissa aberta**, mesmo com todo o resto verde. É portão duro |
| **Se o dono confirmar** | A marca sai, sem refazer nada |
| **Se o dono recusar** | Novo cálculo de impacto. O que dependia da premissa é reaberto |
| **Limite** | Não vale para área sensível de `risk-map.yaml` nem dado `restricted` |

#### Como a documentação é atualizada

| O quê | Quando | Como |
|---|---|---|
| **Seção do refinamento** | Na hora em que o dono aceita | Só a aprovação dessa seção vira `desatualizada`, e o dono aprova de novo (§8.3) |
| **Plano de teste** | Se um critério mudou | Volta ao QA antes de a tarefa recomeçar |
| **Spec congelada** | Depois da reaprovação | Gera `US-1-2.lock.v2.yaml`, com o motivo, sem apagar a `v1` |
| **ADR** | Se a proposta for marcada como decisão de arquitetura | O TL é obrigado a registrar antes de reaprovar |
| **Documentação do sistema** | No fim da história | O writer gera a partir da spec vigente e do código |
| **Lição no cérebro** | No fim da história | Candidata, por PR, como qualquer outra (§13.5) |

#### Como a IA continua

```bash
nexo dev resume US-1-2 --dry-run    # mostra o que será refeito e o custo estimado
nexo dev resume US-1-2
```

1. **Confere** que não há proposta bloqueante aberta e que a versão aprovada é a mais recente. Se não for o caso, explica o que falta e para.
2. **Volta ao checkpoint** da tarefa afetada no Git (`adapters/workspace/git.py`).
3. **Recalcula o impacto** comparando as duas versões da spec.
4. **Refaz só o afetado**, na ordem do grafo. O teste novo ou alterado passa pelo QA antes do código.
5. **Abre uma sessão nova do agente**, em vez de continuar a conversa antiga, para que ele não siga a decisão anterior. Ele recebe uma extensão do pacote de handoff:

```yaml
task_id: T-1-2-3
reason: spec_change
from_lock: v1
to_lock: v2
what_changed:
  - section: tecnica
    before: Usar o arredondamento da API
    after: Calcular os centavos localmente
    why: A API recusa centavos (P-9, aprovada por gh:ana)
done: [T-1-2-1, T-1-2-2]
redo: [T-1-2-3]
keep_from_checkpoint: [src/pricing/parcelas.py]
do_not_touch: [testes bloqueados de AC-1 e AC-2]
```

**Isso funciona em qualquer ferramenta.** Como o pacote não depende da conversa, a retomada pode trocar de rota, por exemplo do Copilot para o Claude Code, como na troca por cota.

**Depois de retomar, nada é dispensado.** A história passa pelos mesmos portões, contra a versão nova. Inclusive as tarefas que continuaram rodando: o que elas produziram é validado contra a `v2`.

#### Rastreamento

Cada problema gera eventos na demanda: `problem.opened`, `problem.decided`, `premise.assumed`, `premise.resolved` e `spec.lock_versioned`. Assim, `nexo demand tree` e o painel mostram quantos problemas surgiram na construção, de que tipo, quanto tempo a história esperou e **quanto custou o retrabalho**. É um retorno direto sobre a qualidade do refinamento: muitos `functional` indicam que o refinamento funcional está deixando lacunas.

### 8.11 Etapa, skill e portão

Cada etapa combina três coisas: **quem é o dono**, **a skill que orienta** e **o portão que não depende de IA**.

| # | Etapa | Dono | Comando | Skills (fábrica + mercado) | Portão determinístico | Artefato |
|---|---|---|---|---|---|---|
| 0 | Preparar | TL | `tl setup` | prompts 10, 11 e 12 do toolkit | `doctor`, integridade do cérebro | `AGENTS.md`, `.nexo/` |
| 1 | Discovery | PM | `pm discover` | `discovery-de-produto`, Superpowers `brainstorming`, prompt 04 | Lacunas de evidência (modelo `Discovery`) | `discovery.md/.yaml` |
| 2 | Refinamento funcional | PM | `pm refine`, `pm answer`, `pm approve` | `requisitos-funcionais`, `historias-de-usuario`, `criterios-de-aceite` | Prontidão 100%, Gherkin, classe de dados | `spec.yaml`, histórias em rascunho |
| 3 | Refinamento técnico | TL | `tl analyze`, `tl spec`, `tl plan`, `tl approve`, `tl assign` | `refinamento-tecnico`, `desenho-tecnico-sdd`, `decisoes-de-arquitetura-adr` | Premissas e lacunas registradas; história com responsável ou na fila | `design`, ADR, primeira quebra |
| 4 | Plano de teste | QA | `qa plan`, `qa approve` | `testes-de-aceite`, `testes-de-integracao` | **Todo AC com cenário** 🔵 | `testes.yaml` |
| 5 | Refinamento do dev | Dev | `dev refine`, `dev approve` | Superpowers `writing-plans`, `task-breakdown` | Validação do backlog, revisão da quebra, sem proposta bloqueante 🔵 | tarefas |
| 6 | Conferir | Automático | — | `handoff-pm-tech-lead` | Quatro partes aprovadas e atuais; hashes conferem | `roles.yaml` |
| 7 | Pronto | Automático (`tl ready` para ver) | — | `definition-of-ready` | **DoR**; spec congelada 🔵 | `refinamento/*.yaml`, `.lock.yaml` |
| 8 | Teste primeiro | QA / agente | `qa write` (ou dentro de `dev run`) | Superpowers `test-driven-development`, `testing-<stack>` | **Testes bloqueados** | testes |
| 9 | Implementar | Dev / agente | `dev run` | Superpowers `executing-plans`, `<stack>-standards` | Política, ambiente mínimo, injeção, teto de custo; dev indicado 🔵 | código, checkpoints |
| 10 | Verificar | Automático | `qa run` | Superpowers `verification-before-completion` | Receita, padrão de código, cobertura do diff | relatórios |
| 11 | Revisar | IA de **outro fornecedor** | automático | `code-review-checklist`, `validacao-de-seguranca`, `requesting-code-review` | **Portões duros + nota de confiança** | veredito |
| 12 | Decidir | TL | `tl pass\|fail` | — | Identidade + segregação | decisão |
| 13 | Entregar | Dev | `dev pr` | Superpowers `finishing-a-development-branch`, `commit-pr-conventions` | Resultado do pipeline existente (só leitura), proteção de branch, evidência do portão local | PR com trailer e evidência |
| 14 | Aceitar | PM | `pm accept` | `validacao-pos-deploy` | Rastreabilidade completa | demanda concluída |
| 15 | Aprender | Automático | `brain propose` | `documentacao-viva` | `brain lint`, redação, PR, assinatura | nota no cérebro |

**Retornos:**

- Portão reprovado → gera bug ligado → volta à etapa 9, com Superpowers `systematic-debugging`.
- Lacuna funcional → `tl return` → etapa 2.
- Ambiguidade durante a execução → evento `spec_ambiguity` → etapa 2.
- Cota esgotada → troca de rota com o pacote de handoff, sem perder contexto.

---

## 9. Skills e fluxos de mercado

### 9.1 O que o toolkit realmente fornece

O próprio repositório declara: *"The toolkit is not a generator or a finished application"*. Ele contém documentação, prompts, templates e dois validadores próprios. Por isso ele é **conteúdo do Nexo, não código**:

| Do toolkit | Vira no Nexo | Adoção |
|---|---|---|
| Prompts 10, 11, 12 (setup) | `nexo tl setup`, com o que era pedir ao agente conduzido e verificado | Adaptado |
| Prompt 04 (brainstorm → plano) | Etapas 1 a 5 | Adaptado |
| Prompts 03, 13, 16 (revisão, segurança) | `reviewer`, `security-reviewer`; a auditoria ampla do prompt 16 fica para depois do MVP | Adaptado |
| Prompts 14, 15 + `harness-evaluation` | Módulo `eval`: testes de comportamento do próprio Nexo | Copiado |
| `templates/project-docs` | Documentos gerados no projeto | Copiado |
| `AGENTS.md.template`, `CLAUDE.md.template` | Instruções geradas por `nexo tl setup` | Copiado |
| Regras ESLint e `verify.mjs` | Receita `react` e padrão de código | Copiado |
| Tríade "arquivo presente, configuração concluída, integração testada" | Estados de `doctor`, `mcp check`, `pack` | Princípio |
| Regra de memória: fonte, evidência, verificação, invalidação | Frontmatter do cérebro (§13.3) | Princípio |
| Catálogo: Context7, Chrome DevTools, Sentry, agent-browser | Entradas do catálogo MCP (§12) | Referência |

### 9.2 Conflitos encontrados e como ficam

| Conflito | Toolkit / Superpowers diz | base faz | Decisão no Nexo |
|---|---|---|---|
| Subagentes | `subagent-driven-development`, ondas paralelas | `policy.yaml`: `Task: deny`, `Agent: deny` | **Mantém a negação.** Quem paraleliza é o Nexo, com worktree por tarefa, porque subagente escapa da trilha e do custo. Essas skills não entram no pack executado. |
| Regra dos 1% | `using-superpowers`: invocar skill antes de qualquer resposta | Determinístico primeiro | **Não adotada.** O Nexo escolhe a skill de cada etapa de forma determinística. |
| Brainstorm interativo | Pergunta em sessão | `run` é não interativo | Vira o ciclo de dúvidas do `pm answer`, já existente. |
| Worktrees | Skill cria | `adapters/workspace/git.py` cria | O Nexo cria. Skill não entra. |
| Hooks de commit | "Não adicione bloqueios de Git" | `guard` e portões | O Nexo não bloqueia `git push` por padrão; o portão vale no fluxo do Nexo (§24). `guard` é opcional. |
| Stack | Next.js como padrão | 7 stacks com receita | Preserva a stack do projeto. Next só em projeto novo por opção. |
| Fonte remota | "Leia o raw do `main`" | Cérebro assinado | **Nunca lê `main` em tempo de execução.** Só commit fixado e com hash (§9.3). |
| Limite de arquivo | 350 linhas | 700 linhas | 350 como aviso e 700 como erro no código do Nexo. |
| Proxy de tokens | RTK, Caveman | Medição própria | Desligados: proxy pode mudar a saída que os leitores interpretam. Candidatos para avaliação com `nexo eval`. |

### 9.3 Formato e governança dos packs

**Formato de skill:** o padrão aberto [Agent Skills](https://agentskills.io) (pasta com `SKILL.md` e frontmatter), que o Superpowers e dezenas de ferramentas já usam. As 63 skills da base já são compatíveis na forma: conferi que todas têm só `name` e `description` no frontmatter e que o `name` é igual ao nome da pasta. Os metadados de governança (escopo, dono, classe de dados) continuam fora do `SKILL.md`, no arquivo de extensão, `extension.yaml`. A conformidade é conferida por `nexo pack lint`.

```yaml
# packs/packs.lock.yaml
packs:
  - id: vibe-coding-toolkit
    source: https://github.com/soumatheusgomes/vibe-coding-toolkit
    commit: 21b71b68679c11184f49c23168852014bf6dde8b
    license: MIT
    tree_sha256: <hash>
    adopted: [prompts/04, prompts/14, templates/project-docs, ...]
  - id: superpowers
    source: https://github.com/obra/superpowers
    commit: <fixar na M4>
    license: MIT
    adopted: [brainstorming, writing-plans, test-driven-development,
              systematic-debugging, verification-before-completion,
              requesting-code-review, receiving-code-review,
              finishing-a-development-branch, executing-plans]
    rejected:
      using-superpowers: conflita com o determinístico primeiro
      subagent-driven-development: política nega subagente
      dispatching-parallel-agents: política nega subagente
      using-git-worktrees: o Nexo gerencia worktrees
```

**Cascata de resolução:** fábrica → pack → cérebro global → cérebro do projeto → `.nexo/skills/`. O mais específico vence, **exceto** chaves bloqueadas do piso.

**Entrada de conteúdo de terceiro (`nexo pack update`):**

1. Baixa o commit pedido numa pasta isolada, sem executar nada.
2. Mostra o diff em relação ao lock.
3. Passa o detector de injeção e a lista de scripts e `allowed-tools`.
4. **Script dentro de skill é bloqueado por padrão** e precisa de aprovação nominal.
5. Roda `nexo eval gate` (depois da M7) para detectar regressão.
6. Abre PR no brain com licença e `NOTICE`.

**Por que tanto cuidado:** skill é instrução executada com as permissões do agente. Relatos secundários citam centenas de skills maliciosas em marketplaces, então o Nexo não instala nada de marketplace direto.

### 9.4 Outros frameworks

| Framework | Uso no Nexo |
|---|---|
| **BMAD** | Referência dos papéis e da passagem de documento entre eles. Não instalado: é pesado e criaria uma segunda fonte de verdade. |
| **GitHub Spec Kit** | Referência da sequência spec → plan → tasks, que o Nexo já faz. Não instalado. |
| **OpenSpec** | Inspiração para mudança em sistema existente (`--delta`). Candidato pós-MVP. |
| **Vercel Skills** | Skills de stack em projetos Next/React, via pack opcional. |

---

## 10. Validações determinísticas

**Tudo aqui roda sem IA e é herdado da base.** A IA pode sugerir, mas não decide nada desta tabela.

| Grupo | Validação | Origem | Bloqueia? |
|---|---|---|---|
| **Entrada** | Prontidão da spec e dúvidas | `domain/spec/{model,questions}` | Sim |
| | Gherkin gerado dos critérios | `domain/spec/gherkin` | Sim |
| | Lacunas de evidência no discovery | `domain/planning/roles` | Sim |
| | Backlog e grafo de paralelismo | `domain/planning/backlog` | Sim |
| | Definition of Ready | `domain/planning/ready` | Sim |
| **Execução** | Política de ferramenta, caminho, comando, rede e MCP | `domain/security/policy` | Sim |
| | Ambiente mínimo sem segredos herdados | `domain/security/environment` | Sim |
| | Conteúdo externo cercado e detecção de injeção | `domain/security/injection` | Sim |
| | Redação de segredos e dados pessoais | `domain/security/redaction` | Sim |
| | Kill switch | `domain/security/killswitch` | Sim |
| | Teto de custo e orçamento | `domain/finops/costs` | Pausa |
| **Código** | Etapas obrigatórias da receita | `adapters/quality/runner`, `domain/quality/model` | Sim |
| | Achado de segurança alto ou crítico | `domain/gates/confidence` | Sim |
| | Critério sem teste ou com teste falhando | `domain/quality/traceability` | Sim |
| | Testes bloqueados alterados | `domain/gates/confidence` | Sim |
| | Revisor reprovou ou achado bloqueante | `domain/gates/confidence` | Sim |
| | Cobertura total e do diff | `domain/gates/confidence` | Sim |
| | Padrão de código por stack | `domain/quality/standards` | Sim |
| | Dependência nova, área de risco | `domain/gates/confidence`, `risk-map.yaml` | Vai a humano |
| **Spec do branch igual à aprovada** 🔵 | `.lock.yaml` + hash por seção | Sim |
| **Sem premissa provisória aberta** 🔵 | `provisional` no lock | Sim, no merge do PR |
| **Sem proposta bloqueante aberta ao retomar** 🔵 | `dev resume` | Sim |
| **Impacto sem certeza pausa a história** 🔵 | `demands.domain.impact` | Pausa |
| | Nota de confiança | `domain/gates/confidence` | Define humano ou automático |
| **Decisão** | Identidade verificada e segregação | `app/execution/story/decisions` | Sim |
| | Aprovação desatualizada | `domain/planning/roles` | Sim |
| | Máquina de estados | `domain/workflow/engine`, `workflows/story.yaml` | Sim |
| **Integridade** | Assinatura Ed25519 do cérebro | `adapters/knowledge/brain` | Sim |
| | Trilha com hash encadeado | `domain/security/audit` | Sim |
| | Telemetria por lista de permissão | `domain/monitoring/telemetry` | Recusa o evento |
| **Novos 🔵** | Todo AC tem cenário no plano de teste | `quality` | Sim |
| | `brain lint`: ID, frontmatter, links | `knowledge` | Sim no PR |
| | Lock do pack, licença e hash | `packs` | Sim |
| | MCP com versão fixada e ferramenta na lista | `mcp` | Sim |
| | Projeção de demanda bate com os eventos | `demands` | Sim em `demand verify` |

**Observação de pureza:** `domain/` não importa nenhuma outra camada, mas usa `os`, `pathlib`, `time`, `random` e `ruamel`. Ao migrar para o `kernel`, relógio, aleatoriedade e YAML passam a ser injetados, para que os testes sejam repetíveis.

---

## 11. Agentes e coleta de informações das CLIs

### 11.1 Estado real de cada ferramenta

Conferido em `resources/agents/*/capabilities.yaml`, no código e nesta máquina em 2026-10-30.

| Ferramenta | Execução na base | Guarda por hook | Leitor de consumo | Nesta máquina |
|---|---|---|---|---|
| **Claude Code** | 🟢 Verificado | 🟢 Pronto | 🟢 `~/.claude/projects/*/*.jsonl` | Instalado |
| **Copilot CLI** | 🟢 Verificado | 🔵 Sem hook | 🟢 `session-store.db`, 🟡 Windows | **Não instalado** |
| **Devin CLI** | 🟢 Verificado | 🔵 O contrato atual diz `native_hooks: false`, mas o formato `.devin/hooks.v1.json` já é usado em outro projeto; provar | 🟢 `sessions.db`, 🟡 Windows | Instalado |

**Trabalhos da M3 que saem daqui:**

1. **Guarda no Devin.** O contrato da base está desatualizado. Atualizá-lo e provar com caso real que uma ferramenta proibida é mesmo bloqueada.
2. **Copilot.** Sem hook disponível, ele roda com `--deny-tool` e, sempre que possível, em contêiner. **Enquanto a guarda não for provada, ele não recebe dados `confidential` ou superiores**, o que pode excluir boa parte do código da empresa. A versão atual precisa ser reavaliada na P0, porque o contrato da base é de 2026-10-02.
3. **Leitores de consumo no Windows.** Os do Copilot e do Devin nunca rodaram no Windows, e o FinOps depende deles.
4. **MCP no Devin.** O contrato da base diz `mcp: false`. Enquanto for assim, MCP só vale no Claude e no Copilot.

**Revisão por outro fornecedor:** Claude, Devin e Copilot atendem à regra, desde que o modelo da revisão seja de outro fornecedor. O Devin e o Copilot podem usar modelos da própria Anthropic, então o roteador compara o **fornecedor do modelo**, não a ferramenta.

### 11.2 Porta do agente

Mantém `AgentPort` da base. Cada agente é um módulo `agents.<id>`, com `capabilities.yaml` e estas capacidades declaradas: `exec`, `non_interactive`, `hooks`, `mcp`, `skills_format`, `usage_source`. **O roteador nunca escolhe uma rota que não tenha a capacidade exigida pela tarefa.**

`verified: true` só vale com evidência: comando, versão da ferramenta, data, resultado. Mudar a versão instalada marca `reverify`, e o monitor sintético (`app/platform/monitor`) detecta formato divergente.

### 11.3 O que o coletor local captura

```text
CLIs (arquivos locais) ──► usage.scan ──► SQLite local ──► outbox ──► central
                               │
                               └─ atribui a projeto pelo caminho da sessão
```

| Captura | Não captura |
|---|---|
| Ferramenta, modelo, dia, sessão, tokens, cache, créditos, custo estimado | Prompts, respostas, código, caminhos (a pasta não sai da máquina) |
| Projeto (slug), se dentro ou fora do Nexo | Nome de arquivo, conteúdo de ferramenta |

Sessão aberta fora do Nexo também é contada, separadamente, permitindo responder "quanto do meu uso de IA passou pelos portões?". Para o consumo da máquina inteira não se misturar, a atribuição é feita pelo caminho; sessão sem projeto conhecido fica em `(sem projeto)`. Liga e desliga por `usage.scan_external`.

---

## 12. Conexão com novos MCPs

**Ponto de partida:** o catálogo atual (`resources/mcp/catalog.yaml`) tem 16 servidores e **todos estão com `verified: false`**. Ele também aceita comandos sem versão fixa, como `npx -y @azure-devops/mcp`, o que permite mudança silenciosa de código. Os dois pontos são corrigidos.

### 12.1 Fluxo

```bash
nexo mcp search postgres              # busca no catálogo e no MCP Registry oficial
nexo mcp add <id> --project           # gera a proposta, ainda sem efeito
nexo mcp approve <id>                 # exige papel tl (ou solo, com aviso)
nexo login mcp_<id>                   # credencial no cofre
nexo mcp check <id>                   # conectou? lista as ferramentas
nexo mcp test <id>                    # chama uma ferramenta de leitura de verdade
```

### 12.2 Regras

| Regra | Detalhe |
|---|---|
| **Registro descobre, não aprova** | O Registry oficial é só índice. Aprovar é uma entrada em `org/mcp-allowlist.yaml`, no cérebro, por PR. |
| **Identidade pelo destino** | A aprovação casa por URL ou comando + versão, não pelo nome, que é só um rótulo. |
| **Versão fixa** | Stdio exige versão exata (ou imagem com digest). `npx -y pacote` sem versão é recusado. |
| **Leitura por padrão** | `tools.allow` lista as ferramentas. Ferramenta de escrita precisa de aprovação nominal e `kind: write`. |
| **Classe de dados** | O `data_policy` define quais classes podem ir para quais fornecedores. |
| **Cofre** | Token só no keyring; ambiente do agente recebe apenas o necessário. |
| **Resposta é dado** | Conteúdo devolvido por um MCP passa pelo cercamento e detector de injeção. |
| **Transpilação** | Uma definição gera `.mcp.json` (Claude) e o formato do Copilot; o Devin depende do item 4 do §11.1. |
| **Estado honesto** | `listed` → `configured` → `connected` → `used`, cada um com evidência e data. |
| **Fluxo crítico não depende de MCP** | Estado da história, PR e aprovação ficam nas integrações nativas, como na base. |

### 12.3 Prova no MVP

Marcar `used` com conta real, no mínimo: **GitHub, Context7 e Chrome DevTools** (este último útil para o QA). Os demais continuam 🟡 até serem usados.

---

## 13. Segundo cérebro

### 13.1 Decisão

**A estrutura e o processo do cérebro atual continuam:** repositório Git, mudança só por PR, manifesto assinado com Ed25519, e o Nexo recusa conteúdo que não bate. **O que muda é que cada arquivo passa a ser um nó identificado e ligado**, o que o torna navegável no Obsidian e pronto para o grafo.

O repositório novo é o `nexo-brain`, **na organização** (D-28), criado a partir do cérebro-base.

### 13.2 Estrutura

```text
nexo-brain/
├── .brain/manifest.json        # assinado (não usar .nexo-brain)
├── .gitignore                  # inclui .obsidian/ e .trash/
├── brain.yaml                  # schema_version, min_nexo
├── INDEX.md                    # 🔵 ponto de entrada no Obsidian (gerado)
├── agents/ evals/ extensions/ insights/ lessons/ org/ prompts/ recipes/
├── routing/ squads/ standards/ templates/ workflows/
├── routing.yaml · risk-map.yaml · CODEOWNERS
├── packs/                      # 🔵 conteúdo adotado + lock
├── decisions/                  # 🔵 ADRs e decisões globais
├── knowledge/                  # 🔵 conhecimento reutilizável promovido
└── projects/                   # 🔵 uma pasta por projeto
    └── <slug>/
        ├── project.yaml        # classe de dados, stack, repositórios
        ├── knowledge/ · lessons/ · decisions/ · runbooks/
        └── _generated/         # resumos do writer, sempre sobrescritos
```

**Isolamento (D-06):** ao montar o contexto, o Nexo entrega ao agente `global + projects/<atual>`. **O filtro acontece antes do índice e do grafo**, para que a lição de um projeto não apareça no prompt de outro. Um teste de contrato garante isso. Projeto `restricted` aponta `brain.url` para um cérebro próprio. A única exceção são os nomes de componentes da linhagem (§23.8).

**Promoção:** um conhecimento de projeto só vai para o global por `nexo brain promote <id>`, em PR com revisão humana e varredura de dados sensíveis.

### 13.3 Frontmatter (contrato para o grafo)

```yaml
---
id: lesson-idempotencia-sqs      # igual ao nome do arquivo; único no cérebro
type: lesson                     # lesson | decision | knowledge | runbook | prompt | standard | ...
title: Consumidor SQS precisa de chave de idempotência
scope: project:loja              # global | project:<slug>
status: active                   # draft | active | deprecated
tags: [aws, sqs, resiliencia]
links:                           # arestas tipadas
  - { rel: derived_from, to: decision-mensageria }
  - { rel: applies_to, to: skill:aws-mensageria-sqs-sns }
source: { kind: pr, ref: "github:minha-org/loja#42" }
data_class: internal
verified_at: 2026-10-30
invalidate_when: troca de broker de mensageria
---

Texto da nota, com [[decision-mensageria]] no meio do conteúdo.
```

| Regra de `nexo brain lint` | Motivo |
|---|---|
| `id` igual ao nome do arquivo, único | O Obsidian resolve `[[link]]` pelo nome do arquivo |
| `SKILL.md` mantém o nome exigido pelo padrão; ID em `metadata.id`, link como `[[extensions/skills/x/SKILL\|x]]` | Compatibilidade com Agent Skills |
| Todo `[[link]]` e `links[].to` existe | Sem link quebrado |
| `rel` pertence ao vocabulário fechado | Grafo consultável |
| `scope` bate com a pasta | Isolamento |
| `verified_at` vencido gera aviso | Toolkit: conhecimento tem validade |
| Sem segredo ou dado pessoal; `data_class` ≤ a do projeto | Regra da base |
| YAML continua YAML; arquivos centrais ganham nota espelho quando precisarem de links | Não quebrar os consumidores |

**Vocabulário de arestas:** `links_to` (wikilink), `derived_from`, `supersedes`, `applies_to`, `depends_on`, `contradicts`, `part_of`, `mentions`.

### 13.4 Grafo: o que entra agora e o que fica para depois

A base já tem um índice determinístico (`domain/knowledge/graph.py`) com arquivos, testes, histórias, critérios, tarefas, incidentes, lições e ADRs. Ele ainda não vê frontmatter nem wikilinks.

| | MVP (preparação) | Depois (`brain.graph`, experimental) |
|---|---|---|
| Identidade | IDs, frontmatter e lint em todo o cérebro | — |
| Extração | `nexo brain index` gera `nodes.jsonl` e `edges.jsonl` determinísticos, no cache local | Mesmo formato |
| Busca | `nexo brain search` por texto, tag e tipo | Travessia: "o que se liga a X em até 2 saltos" |
| Contexto do agente | Seleção por escopo, tag e `applies_to` | Seleção por vizinhança + impacto do código |
| Mesclagem com o índice de código | Formato compatível | Grafo único código + cérebro |
| Ferramenta para o agente | `nexo-mcp: brain_search` | `brain_neighbors`, `brain_path` |
| Embeddings | Não | Avaliar só com `nexo eval` mostrando ganho |

**O grafo é sempre derivado e nunca versionado.** O Git é a fonte. A chave do cache é a versão do cérebro, então a mudança de versão refaz o índice.

### 13.5 Como a IA escreve e como o Obsidian entra

```text
Execução / PR ──► writer ──► brain lint + redação ──► branch + PR ──► você revisa ──► merge ──► nexo brain sign
                                                                                                    │
Obsidian (clone de trabalho) ──► nexo brain propose ──► mesmo PR                                    ▼
                                                                                     nexo brain pull (cópia verificada)
                                                                                                    │
                                                                                                    ▼
                                                                                             Agentes leem daqui
```

**Duas cópias, de propósito:**

| Cópia | Local | Quem usa | Pode editar? |
|---|---|---|---|
| **Clone de trabalho** | Onde você escolher | Você e o Obsidian | Sim. Nada chega ao agente sem PR |
| **Cópia verificada** | `~/.local/share/nexo/brain/` | Agentes | Não. `pull` recusa se a assinatura não bater |

Isso resolve o conflito sem dividir o repositório: abrir, reorganizar ou editar no Obsidian nunca quebra a execução.

**Duas correções em relação à base:**

1. `_files()` em `brain.py` assina **tudo** que está em disco exceto `.git` e a pasta do manifesto. Assinar a partir de um clone com `.obsidian/` incluiria os arquivos da ferramenta. O Nexo passa a assinar pelos arquivos rastreados no Git (`git ls-files`) e recusa assinar com árvore suja.
2. O `brain propose` atual só abre **issue** com o conteúdo como texto; o PR existe apenas no `docs knowledge`. No Nexo, `propose` sempre abre branch + PR, com a issue como alternativa sem permissão de escrita.

**Custo desta escolha:** uma nota nova só chega aos agentes depois de PR, merge e assinatura. **Não há merge automático**, porque ele dependeria de CI.

**Lint sem CI:** `brain lint` roda em três pontos: `nexo brain propose` não abre PR se falhar, o revisor confere com `nexo brain review <pr>`, e **`nexo brain sign` recusa assinar se falhar**. Como o agente só lê uma versão assinada, um PR que tenha escapado do lint não chega a ele.

**Assinatura:** `nexo brain sign` é manual, e a chave não pode depender de uma só pessoa nem ficar numa máquina pessoal. A base só aceita chave local ou Azure Key Vault; o cofre da empresa é o item Q-22.

---

## 14. Rastreamento das demandas

### 14.1 O que é uma demanda

**Toda unidade de trabalho que o Nexo cria ou recebe**, seja por uma pessoa ou **gerada pela própria execução**. É aqui que o pedido "demandas geradas pela execução em outro projeto" se resolve.

| Origem | Exemplo | Quem gera |
|---|---|---|
| `manual` | `nexo pm idea` | Pessoa |
| `planning` | Feature, história, tarefa | Aprovação do plano |
| `gate` | Bug de portão reprovado | Execução |
| `qa` | `nexo qa bug` | QA |
| `review` | Achado da revisão de IA ou do PR | Execução |
| `clarification` | Dúvida do agente que travou a tarefa | Execução |
| `deps` | Vulnerabilidade | Varredura |
| `incident` | Incidente | Alerta |
| `learning` | Lição candidata ao cérebro | Aprendizado |
| `lineage` | Mudança de contrato que afeta consumidores conhecidos, inclusive de outro time | Linhagem (§23.7) |
| `followup` | Pendência declarada no fim da tarefa | Agente, em campo estruturado |

Demandas nascem com `spawned_by`, formando uma árvore. Dá para responder "o que essa feature gerou?" e "quanto retrabalho veio de portão?".

**Limite contra ruído:** demandas de origem `review`, `followup` e `learning` geradas por IA nascem como `triage` e não aparecem como trabalho até alguém aceitar. As de `gate` e `qa` já nascem ativas, porque têm evidência determinística.

### 14.2 Modelo

```yaml
id: loja:BUG-1-2-1              # <slug do projeto>:<id local>, único entre projetos
type: bug                       # idea|epic|feature|story|task|bug|finding|vuln|incident|lesson
title: Arredondamento errado em 3 parcelas
state: EmDev                    # mesma máquina de workflows/story.yaml + Triage
stage: build                    # etapa do §8.2
holders:                        # calculado; nunca editado à mão
  - { role: dev, who: gh:dani, action: "Corrigir o bug", since: 2026-10-30T14:02 }
responsible: { pm: gh:bia, tl: gh:ana, qa: gh:caio, dev: gh:dani }
origin: { kind: gate, run: R-88, gate: hard, ref: "AC-3 falhou" }
spawned_by: loja:US-1-2
parent: loja:US-1-2
links: { pr: "github:minha-org/loja#42", commits: [abc123], external: "github:minha-org/loja#57" }
data_class: internal
cost_brl: 1.42                  # calculado a partir das execuções
created_at: 2026-10-30T14:02:11-03:00
updated_at: 2026-10-30T15:20:45-03:00
```

### 14.3 Armazenamento: o repositório do time

**Eventos são a fonte, e o estado atual é uma projeção**.

**Um repositório comum, `nexo-<time>`, na organização, com apenas o branch `main` e sem workflow.** Nenhum branch novo é criado nos repositórios de código.

```text
nexo-time-posicao/
├── team.yaml                        # pessoas, papéis e modo
├── .gitattributes                   # * text eol=lf, para o hash ser igual no Windows
├── projects/
│   └── gestao-posicao/
│       ├── demands/
│       │   ├── events/2026-10/01JB….json    # um arquivo por evento, nunca editado
│       │   └── current/BUG-1-2-1.yaml       # projeção, regenerável
│       ├── ideas/IDEA-1.yaml
│       ├── specs/F-1/
│       │   ├── sections/funcional.md · tecnica.md
│       │   ├── stories/US-1-2/{funcional,testes,implementacao}.md
│       │   └── roles.yaml                   # aprovações com hash por seção
│       ├── proposals/P-7.yaml
│       └── meetings/M-1/{sessao.yaml,transcricao-redigida.md,itens.yaml}
└── lineage/                         # um manifesto por repositório de código (§23.4)
```

| Por que um repositório do time | Efeito |
|---|---|
| Sem branch especial nos repositórios de código | Respeita a política e não depende de exceção |
| Separado do código | Não entra no diff da história nem na revisão, e não aciona o pipeline dos serviços |
| **Vários projetos no mesmo lugar** | O board do time funciona sem servidor nenhum |
| PM e QA clonam só este repositório | Não precisam de acesso nem de espaço para o código |
| Um arquivo por evento com ID ordenável | Sem conflito de merge entre várias pessoas |
| Funciona offline e é versionado | Mudanças ficam na fila e são enviadas depois |

**Alternativas descartadas:**

- **Referências Git fora de branches** (`refs/nexo/*`): não seriam branches na prática, mas seria um jeito de contornar a política. Se a empresa não permite, o Nexo não burla.
- **Branch comum de longa duração:** teria os arquivos de workflow do código, então cada sincronização poderia disparar o pipeline predefinido.

**Se não for possível criar o repositório (Q-17):** o armazenamento fica atrás de uma porta (`TrackingStore`), que também aceita um bucket S3 da empresa. Sem nenhum dos dois, `tracking.mode: home` grava em `<dados do usuário>/tracking/<slug>/` e **não compartilha nada entre pessoas**, o que inviabiliza o fluxo entre papéis (§8.8).

**Visibilidade:** o repositório é interno, nunca público. `nexo init` recusa um repositório do time público, porque ele guarda specs e transcrições redigidas.

`nexo demand verify` refaz a projeção a partir dos eventos e falha se houver diferença. Cada evento guarda o hash da entrada correspondente na trilha de auditoria.

### 14.4 Sincronização externa

Ponto de partida: os adaptadores da base, com a porta `Tracker` (`create`, `transition`, `comment`, `fetch`). 🟡 O GitHub foi testado com conta real como repositório (push, PR, CI), mas o **GitHub Issues** só rodou com simulador. Por isso a prova entra na M5.

**Removidos:** Jira, Linear, Azure Boards e GitLab Issues. `adapters/trackers/{jira,linear,azure,gitlab_issues}.py` (cerca de 630 linhas), seus de-paras e testes **não são copiados**. A porta `Tracker` continua, então uma ferramenta nova pode voltar como módulo.

| Regra | Comportamento |
|---|---|
| **Escolha por projeto** | `tracker.kind`: `local`, `github` ou `file` (CSV, sem API) |
| **Fila com repetição** | Falha externa nunca trava o trabalho. Fica no outbox e tenta de novo |
| **Idempotência** | A chave é o ID do evento. Reenviar não duplica |
| **De-para versionado** | `tracker map --check` valida antes de sincronizar |
| **Estado de portão pertence ao Nexo** | `Pronto`, `EmQA`, `AguardandoHumano`, `Concluido` não podem ser forçados de fora: mudança manual vira alerta, não transição |
| **Responsável pertence ao Nexo** | `tl assign` atualiza o responsável da issue. Mudar direto no GitHub vira proposta para o TL, não uma troca imediata |
| **Campos externos pertencem à ferramenta** | Prioridade, milestone e comentários vêm de lá |
| **Cancelamento** | Cancelar lá propõe cancelar aqui, com confirmação |
| **Ligação com código** | Trailer `Nexo-Demand:` nos commits e link no PR |

**Prova no MVP:** GitHub Issues e Projects com conta real.

**O que esta remoção não muda:** os adaptadores de repositório (`adapters/scm`) e as entradas `atlassian`, `azure-devops`, `gitlab` e `linear` do catálogo MCP são coisas separadas. Os MCPs ficam como ferramentas extras de leitura para o agente, e não são usados para rastrear demandas.

### 14.5 Visão consolidada

```bash
$ nexo board --blocked
PROJETO   DEMANDA      TIPO   ESTADO             DONO  IDADE  ORIGEM  CUSTO
loja      BUG-1-2-1    bug    EmDev              dev   2d     gate    R$ 1,42
loja      US-1-3       story  AguardandoHumano   tl    6h     plan    R$ 8,10
tripway   T-4-2        task   Bloqueado (cota)   dev   1d     plan    R$ 0,31

3 bloqueadas · 1 aguardando você · R$ 9,83
```

| Fonte | Quando | Observação |
|---|---|---|
| **Central** (padrão) | `central.url` configurado | Todos os projetos e máquinas; dados podem ter até alguns minutos de atraso |
| **Local** | Sem central ou offline | Lê os projetos de `~/.config/nexo/projects.yaml`; mostra "dados de hoje 10:32" por projeto |

**Filtros:** projeto, papel, estado, origem, responsável, idade, bloqueio e `--triage`.

**Métricas por demanda:** tempo em cada estado, lead time, tempo bloqueado, reprovações de portão, bugs gerados por história, custo em reais e distribuição por origem.

**Cada projeto se registra no `nexo init`,** o que cobre diretamente o caso "execução em outro projeto". Rodar o Nexo em qualquer repositório faz esse projeto aparecer no board, sem configuração extra além do `init`.

---

## 15. Coletor central

### 15.1 O painel da empresa

**Objetivo:** acompanhar FinOps e métricas de squads num lugar só. É o motivo de o central estar no MVP (D-08). A base tem as páginas abaixo, servidas sem JavaScript, mas 🟡 **não encontrei registro de operação real**: tudo passou só por testes e simuladores, e o parecer de 12/10 trata a instalação como futura.

| Página | Responde | Quem vê | Estado |
|---|---|---|---|
| **Executiva** | Quanto entregamos, a que custo e prazo, comparado à linha de base | Direção, gestão, FinOps | 🟡 |
| **FinOps** | Quanto custa por time, história e ferramenta; orçamento; licença ociosa; uso fora do Nexo; fatura × medido | FinOps, gestão | 🟡 |
| **Squads** | Fluxo, gargalos, tempo por etapa, retrabalho | Devs e líderes do time, gestão | 🟡 |
| **Qualidade** | Falhas de portão, cobertura, escapes, problemas na construção | Líderes, Segurança | 🟡 |
| **Ferramentas** | Rotas, modelos e resultado de cada um | Time do Nexo | 🟡 |
| **Board** | Demandas de todos os times, com quem estão e o que está parado | Todos | 🔵 |
| **DORA e ROI** | Frequência, lead time, falhas e retorno, com as premissas à vista | Direção, gestão | 🟡, entrada muda (§15.3) |
| **Linhagem** | Cobertura por time (M8) | Todos | 🔵 |
| **Frota** | Versões do Nexo e do cérebro nas máquinas | Time do Nexo, Segurança | 🟡 |
| **Meu painel** | O que é meu: histórias, custo, orçamento | Cada pessoa | 🟡 |

**Métricas de squad:** lead time e tempo em cada etapa do §8.2, vazão, demandas paradas, reprovações de portão, primeira entrega certa, bugs por história, problemas por tipo (§8.10), premissas provisórias, custo por história e por ponto, adesão ao Nexo.

### 15.2 FinOps: de onde vem o custo

| Ferramenta | Medido | Origem | Limite |
|---|---|---|---|
| **Claude Code** | Tokens e cache | `~/.claude/projects/*/*.jsonl` | Em assinatura, é preço de referência, não o gasto real |
| **Devin** | ACU | `sessions.db` | ACU diferente de zero ainda não foi confirmado |
| **Copilot** | Créditos ou pedidos premium | `session-store.db` | A unidade cobrada depende do contrato |

| Regra | Comportamento |
|---|---|
| **Sempre em reais** | Converte com `nexo cost pricing`, definido pelo FinOps. **Sem preço de contrato, o painel marca "estimado"** |
| **Fatura manda** | `nexo cost reconcile --invoice` compara o medido com a fatura e mostra a diferença |
| **Custo de uso e custo alocado** | Assinaturas são rateadas à parte, para não parecer que uma história custou o preço da licença |
| **Centro de custo** | `nexo cost chargeback` por squad |
| **Uso fora do Nexo** | Só agregado por dia, atribuído por projeto; sessão fora de projeto conhecido da empresa não é enviada |

### 15.3 Privacidade e DORA

**Métrica de processo, não de pessoa:**

- o central guarda a pessoa pseudonimizada;
- a matriz de visibilidade é **aplicada no servidor**, e entra no MVP;
- páginas por pessoa só para orçamento, bloqueios, portões pendentes e o próprio painel;
- **sem ranking de pessoas**, e cada nome exibido fica registrado na auditoria.

Isso é decisão de produto: painel usado para avaliar pessoas faz os times esconderem problemas.

**DORA sem Actions.** A base recebia as implantações por um workflow (`deploy/ci/dora-deploy.yml`), o que não é possível aqui. O central passa a **consultar a API do GitHub**, só leitura: PRs, merges, releases e Deployments. A frequência de implantação depende de o pipeline predefinido registrar algo lá (Q-23). Taxa de falha exige uma fonte de incidentes, então fica como `sem dado` até existir. **O painel não mostra número sem fonte.**

**Linha de base antes de adotar.** Para o ROI ter comparação, `nexo metrics baseline` coleta o histórico do GitHub **antes do primeiro uso real**. Depois de adotado, esse dado não é mais recuperável com a mesma qualidade.

### 15.4 Contrato de telemetria

O contrato é **`nexo.telemetry/v1`**, com uma regra central: **a CLI projeta só o permitido; o coletor recusa o evento inteiro se vier campo fora da lista.** Módulo novo declara seus eventos e campos no manifesto; a lista final é gerada e versionada em teste de snapshot, de modo que nenhum campo novo saia sem aparecer no diff.

**Eventos novos:** `demand.created`, `demand.transitioned`, `demand.linked`, `demand.triaged`, `usage.day`.

**Telemetria desde a M2.** Eventos entram no outbox assim que o fluxo existe, mesmo sem central. Assim, quando o painel chegar na M7, o histórico já estará lá.

**Exceção controlada para o título da demanda.** O contrato proíbe texto livre, mas um board só com IDs não serve. O campo `title`:

- só é aceito em `demand.*`;
- passa pela redação na CLI e de novo no coletor;
- tem no máximo 120 caracteres;
- é controlado por `telemetry.demand_titles`, com **padrão desligado** para `data_class` `confidential` ou `restricted`.

Nunca saem: prompts, respostas, diffs, caminhos e corpo de issue.

### 15.5 Implantação na AWS

**É um serviço interno da empresa, operado pelo time do Nexo** (D-32).

```text
Máquinas das pessoas ──┐
Navegador (portal) ────┼─► rede interna ─► ALB interno, com TLS ─► ECS Fargate: nexo-central ─► RDS PostgreSQL
                       │                                             │
                       └─ nunca pela internet                        ├─► Secrets Manager: chaves e OIDC
                                                                     ├─► CloudWatch: logs e alarmes
                                                                     └─► KMS: criptografia em repouso
```

| Item | Padrão | Por quê |
|---|---|---|
| **Computação** | ECS Fargate, com 2 tarefas em zonas diferentes e escala automática | Sem servidor para administrar. O agendamento do coletor já evita rodar um mesmo job duas vezes em várias instâncias |
| **Banco** | RDS PostgreSQL, Multi-AZ em produção, com recuperação até um ponto no tempo | Fatos e histórico são a base do painel |
| **Entrada** | ALB interno com certificado da empresa, sem endereço público | Dados de custo e flags não vão para a internet |
| **Segredos** | Secrets Manager: chave de pseudônimo, tokens e OIDC | A chave de pseudônimo perdida impede reidentificar, e vazada desfaz o pseudônimo |
| **Migrações** | Tarefa ECS única, antes de cada versão, com backup | Evita migração concorrente entre tarefas |
| **Observabilidade** | Logs e alarmes no CloudWatch: erro 5xx, atraso de ingestão, conexões do banco e falha de job | O time do Nexo precisa saber antes do usuário |
| **Infraestrutura** | Terraform, em repositório próprio, seguindo a skill `terraform-aws` | Reprodutível, com dev e produção separados |
| **Implantação** | Pelo pipeline predefinido da empresa. **O Nexo não cria workflow** (D-27) | Se o padrão não atender, é o único item que exige conversa com a plataforma (Q-21) |
| **Login no portal** | Por pessoa, em rede interna, até o SSO da empresa ser conectado (§26.3) | |
| **Máquinas** | Token por dispositivo, com pareamento, rotação e revogação (`nexo central pair`) | |
| **Fora da rede** | O outbox guarda e reenvia quando a máquina voltar à rede ou à VPN. **Nunca bloqueia o trabalho** | |
| **Retenção** | 365 dias, a validar. Backups pelo RDS | |
| **Dimensão inicial** | 0,5 vCPU e 1 GB por tarefa, e o menor RDS de uso geral. **É um ponto de partida, sem medição**; ajustar com carga real | Na base, dois coletores ocupavam de 0,5 a 0,9 GB |

**Custo do próprio serviço:** entra no FinOps como custo de plataforma, medido pela tag `service=nexo-central`. O valor depende da conta e da região, então será calculado com a calculadora da AWS na implantação.

```bash
nexo central pair        # pareia a máquina com o central da empresa
nexo central status      # saúde, atraso do outbox, última ingestão, versão
nexo central up          # só para teste local: SQLite em 127.0.0.1
```

**Fica desligado por flag no início:** resumo semanal, alertas em tempo real e autonomia por squad. **Fica para depois:** SIEM, export para BI e aprovação remota. Os 16 detectores de insights entram desligados e são ligados um a um, depois de conferidos com dados reais.

---

## 16. Segurança e dados

| Tema | Regra |
|---|---|
| **Classe de dados** | `public`, `internal`, `confidential`, `restricted`, por projeto e por feature. A IA sugere e uma pessoa decide |
| **Fornecedor por classe** | `data_policy` define o que pode ir para quem. Sem política, nega |
| **Segredos** | Só no keyring. Bloqueados no ambiente do agente e em `forbidden_paths`, incluindo `.nexo/nexo.yaml` para escrita |
| **Conteúdo externo** | Skill, resposta MCP, issue e PR entram como dado, cercados, com detector |
| **Agente** | Sem subagentes. Comandos por lista de permissão. Worktree isolada |
| **Auditoria** | Local, encadeada, com âncora no central |
| **Kill switch** | Bloqueia ferramenta, modelo, versão do Nexo ou do cérebro, em até 2 minutos |
| **Dados de uso** | Telemetria pseudonimizada e matriz de visibilidade (§15.3) |

Toda mudança em `kernel/security`, `kernel/audit`, assinatura e identidade exige **revisão de um segundo fornecedor de IA** antes do merge, no próprio desenvolvimento do Nexo.

---

## 17. Arquivos no projeto e na máquina

```text
<repositório de código>/
├── .nexo/
│   ├── nexo.yaml           # marcador: projeto, stack, time, classe de dados
│   ├── specs/US-n.lock.yaml  # spec congelada, só no branch da história
│   ├── skills/             # skills só deste projeto
│   ├── quality/ evidencias/ map/   # no .gitignore
│   └── .gitignore
└── AGENTS.md · CLAUDE.md   # gerados pelo tl setup, preservando o que já existia

%APPDATA%\nexo\             # NX_CONFIG_HOME (macOS e Linux: ~/.config/nexo)
├── config.yaml · projects.yaml · brain-anchor.yaml

%LOCALAPPDATA%\nexo\        # NX_DATA_HOME (macOS e Linux: ~/.local/share/nexo)
├── nexo.db                 # execuções, consumo, outbox, auditoria
├── brain\                  # cópia verificada
├── teams\<time>\           # cópia do repositório do time
├── wt\<hash>\              # worktrees, em caminho curto (§25.2)
├── runs\ · tools\
└── tracking\<slug>\        # só em tracking.mode: home
```

As pastas vêm de `platformdirs`.

**Configuração em cascata (herdada):** fábrica → cérebro global → cérebro do projeto → projeto → pessoal, com chaves bloqueadas.

---

## 18. Plano de extração da base

### 18.1 O que muda em cada camada

| Camada | LOC | Acoplamento medido | Ação |
|---|---|---|---|
| `domain/` | 8.400 | Zero import de outra camada | **Copiar** para `kernel` ou `modules/*/domain` |
| `adapters/` | 7.600 | Não importa `app/` nem `cli/` | **Copiar** para cada módulo, atrás de porta |
| `store/` | 600 | Só domínio | **Copiar** |
| `collector/` | 7.600 | Só domínio | **Copiar e cortar** (§15.1) |
| `app/` | 20.000 | **Ciclos entre todos os contextos** | **Reorganizar** |
| `cli/` | 7.200 | Só `app` e `domain` | **Reescrever a superfície**, manter `session`, `look`, `render` |
| `resources/` | — | — | Copiar para os módulos e ajustar o frontmatter |
| `tests/` | 115 arquivos | Importam o pacote antigo | Mover junto de cada módulo: é a rede de segurança da extração |

### 18.2 Como quebrar os ciclos de `app/`

Os maiores acoplamentos medidos e o tratamento:

| Dependência | Importações | Tratamento |
|---|---|---|
| `execution → platform` | 37 | `flags`, `config`, `identity`, `killswitch` vão para o `kernel` |
| `execution → planning` | 26 | Porta `SpecReader`: execução lê a spec, sem conhecer o fluxo de planejamento |
| `execution → knowledge` | 23 | Porta `ContextProvider`: entrega o contexto do agente |
| `platform → *` | 2 a 10 cada | `autopilot`, `pending`, `assistant` saem do kernel e assinam eventos |
| `delivery ↔ execution` | 13 / 11 | `gate.approved` → delivery abre PR; `pr.feedback` → execution reabre o ciclo |
| `execution → finops` | 13 | Porta `CostMeter` no kernel |
| `execution → integrations`, `delivery → integrations` | 5, 8 | Eventos: `demands` e `notify` assinam |

**Critério de saída:** contratos do import-linter sem exceção e sem `allow_indirect_imports`.

### 18.3 Método

1. **Copiar com o teste junto.** Nenhum arquivo entra sem os seus testes, e cada lote termina com `pytest` verde.
2. **Renomear de forma mecânica** (pacote para `nexo`, variáveis para `NX_`), por script revisável, com o teste do §3 ativo.
3. **Ponto de partida limpo.** O repositório nasce com histórico novo, sem trazer o histórico Git da base. Só entra o necessário: nada de `.venv`, caches, CHANGELOG, `spec/`, `docs/` ou relatórios antigos. Docstrings, comentários, testes, fixtures e recursos são limpos de nomes, códigos de fase e referências antigas. **A validação falha se um termo antigo aparecer**, comparando com uma lista mantida fora do repositório (`NX_FORBIDDEN_TERMS`).
4. **Não melhorar durante a cópia.** Extração e mudança de comportamento ficam em PRs separados.
5. **Sem compatibilidade com nada anterior.** O Nexo não lê pastas, variáveis ou formatos antigos. Saem o código de migração e os comandos fundidos ocultos.

---

## 19. Plano de entregas

Sem data de calendário porque ainda não há medição de velocidade. O tamanho é relativo. **Cada entrega termina com uma demonstração automatizada** com `fake.py` e uma prova com ferramenta real.

| Fase | Conteúdo | Tamanho | Pronto quando |
|---|---|---|---|
| **P0 — Prova do ambiente** | Registro da autorização (§25.1) e testes descartáveis de GitHub, AWS e CLIs de IA (§25.3). **Sem código de produto** | P | Cada item do grupo A do §25.3 com resultado registrado. Bloqueio vira mudança na spec antes da M0 |
| **M0 — Esqueleto** | Repo, validação local (§20), kernel de módulos, flags, manifesto, kit de contrato, CLI montada do manifesto, teste de nomes reservados, instalador Windows | M | Um módulo `hello` entra só com uma pasta; desligá-lo some com o comando; `nexo flags explain` mostra a fonte |
| **M1 — Piso** | Política, redação, injeção, ambiente, auditoria, kill switch, identidade, portões, store | M | Testes herdados verdes; import-linter sem exceções; `audit verify` detecta adulteração |
| **M2 — Planejar com papéis** | `IDEA-n`, `pm split`, histórias em rascunho, etapas e `holders`, `tl assign`, seções com dono, propostas, reuniões, repositório do time com sincronização automática, spec congelada, `team.yaml`, modo solo, `nexo pm`, `tl`, `qa`, `dev refine`, `brief`, console | **GG** | Demo com `fake` e **duas máquinas simuladas**: da ideia ao DoR, com a demanda aparecendo para a pessoa certa em cada etapa. Proposta aceita invalida só a seção alterada. O portão recusa código com lock antigo |
| **M3 — Executar** | `execution`, `quality`, `sandbox` (`nexo try`, `nexo test`), agentes, roteamento, `nexo dev`, `nexo qa`, `dev problem`, cálculo de impacto, versões do lock, retomada parcial, premissa provisória, leitor Codex, guardas | **GG** | **Uma história real com cada uma das 3 ferramentas**, com teste bloqueado, portão e bloqueio de política comprovados, e revisão por outro fornecedor. **Prova de mudança no meio:** um problema técnico aberto durante `dev run` pausa só a tarefa afetada; a retomada, em outra ferramenta, refaz só ela e passa contra a `v2`; premissa aberta segura o merge |
| **M4 — Packs** | Formato, lock, conflitos do §9.2, Superpowers e toolkit adotados, transpilação, `tl setup` | M | Mudança de 1 byte no pack falha no lock; script em skill é bloqueado; skills descobertas de verdade nas 3 CLIs |
| **M5 — Demandas e entrega** | `demands`, árvore de demandas, `delivery`, GitHub Issues real com responsável e etiqueta, `nexo board` local | G | Executar nos **2 projetos reais** e vê-los no mesmo board; bug de portão aparece ligado à história; `demand verify` passa; reenviar não duplica no GitHub |
| **M6 — Brain** | `nexo-brain`, frontmatter em tudo, `lint`, `index`, `propose` por PR, assinatura por `git ls-files`, isolamento por projeto | M | Vault abre no Obsidian com grafo sem link quebrado; editar nele não quebra `pull`; lição do projeto A não aparece no contexto do B |
| **M7 — Central, portal e MCP** | Coletor no ECS com RDS, contrato `nexo.telemetry/v1`, **portal com gestão de features e insights (§26)**, login por pessoa com a porta `AuthProvider`, páginas do §15.1, matriz de visibilidade, preços de contrato, DORA pela API do GitHub, flags remotas, `nexo mcp`, 3 MCPs provados | **GG** | Máquinas de squads diferentes no mesmo painel; custo bate com a fatura de um mês, dentro da margem acordada com o FinOps; quem é de um squad não vê o detalhe de outro, comprovado por teste no servidor; central derrubado não trava `dev run` e o outbox reenvia sem duplicar; desligar flag no portal vale em até 2 min, com o motivo na trilha; tentar desligar o piso é recusado no servidor |

| Fase seguinte | Conteúdo | Tamanho | Pronto quando |
|---|---|---|---|
| **M8 — Linhagem** | `lineage`, scanners .NET e Terraform, manifesto, publicação em `nexo-<time>/lineage/`, descoberta dos times, validação e consolidação no cliente, gatilhos e agente local, `show`, `impact`, `coverage`, aviso entre times, exportação para Obsidian. Depois: `discover aws` e `kafka` | G | Em repositórios .NET **reais e sem nenhum workflow**: gestão de posição e tradutor em repositórios separados aparecem ligados pelo tópico. Um merge na `main` aparece sozinho em até 1 hora, desde que uma máquina do time esteja ligada. **Duas máquinas publicando ao mesmo tempo não duplicam, e uma atrasada não sobrescreve a mais nova.** Um scan nunca altera a pasta de trabalho. Manifesto adulterado é ignorado na leitura. Um time sem o fluxo do Nexo entra com um YAML ou só com o agente. Consumo só visto na infraestrutura aparece como `não identificado`. Medir acertos e erros do scanner com uma lista conferida à mão, antes de usar a linhagem no portão |

| **MF — Testes finais no Windows** | Grupo B do §25.3, `nexo selftest` e os fluxos de M2, M3, M5 e M7 em máquina Windows da empresa. Inclui adaptadores `schtasks` e Credential Manager, leitores de consumo e instalador | M a G | Fluxo completo de PM, QA, TL e dev no Windows, sem tratamento especial. **Sem isso, o Nexo não é liberado às pessoas** |

**A P0 vem antes de tudo. MVP = M0 a M7 + MF.** A M6 já define IDs e o formato de nós e arestas que a M8 usa. Como D-08 trouxe o central para o MVP, o **primeiro uso útil chega na M5**, com o board local, sem precisar esperar o serviço. O painel chega só na M7, mas os eventos são gravados desde a M2 e a linha de base é coletada antes do primeiro uso. Um tamanho **G** representa várias fases da base. A M2 ficou com **GG** porque concentra ideia, etapas, responsáveis, propostas, reuniões e sincronização entre máquinas, quase tudo novo em relação à base. A M3 também subiu para **GG**, por causa da retomada parcial depois de mudança na spec.

**Depois do MVP:** conexão com o SSO da empresa (§26.3, sem mudar o restante), `eval` e harness, `brain.graph`, `deps`, `legacy`, `program`, `notify`, `autonomy`, `isolation`, `docsite`, insights, skills do Nexo expostas dentro das próprias CLIs de IA (`/nexo-pm-refine`).

### 19.1 Critérios do MVP

Da perspectiva de cada papel:

- [ ] **PM:** sem conhecer Git, vai de uma frase ao requisito aprovado, sabendo o custo estimado.
- [ ] **QA:** vê todo critério sem teste, registra um bug e verifica a correção sem pedir ajuda.
- [ ] **Tech lead:** entende o porquê de um portão reprovado em menos de um minuto, a partir de `nexo tl gate`.
- [ ] **Dev:** roda uma tarefa na ferramenta disponível e, se a cota acabar, a tarefa continua em outra.
- [ ] **Passagem:** em dois computadores, a demanda chega à pessoa certa sem ninguém usar Git, e ela entende o que fazer só com `nexo brief`.
- [ ] **Tech lead:** indica dev e QA por história; só o dev indicado consegue rodar sem aviso.
- [ ] **Problema no meio:** o dev abre um problema sem saber a quem recorrer; o dono certo recebe, decide, e a IA continua refazendo só o afetado, sem ninguém explicar o contexto de novo.
- [ ] **Teste livre:** `nexo try` roda sem criar demanda e não consegue abrir PR nem aprovar nada.
- [ ] **Todos:** `nexo board` mostra as demandas de pelo menos 2 projetos, inclusive as geradas pela execução.
- [ ] **Extensão:** um módulo novo entra sem editar arquivos centrais e é desligado por flag sem release.
- [ ] **Controle:** nenhuma flag desliga o piso, comprovado por teste.
- [ ] **Teste de uso:** uma pessoa de cada papel executa o fluxo só com a ajuda da CLI. O Nexo não está pronto só com testes automáticos verdes, porque a dificuldade de uso é justamente um dos motivos deste projeto.

---

## 20. Qualidade do próprio Nexo

**Tudo roda localmente**, por `python scripts/validate.py`, porque o projeto não conta com GitHub Actions (D-27). Herda a validação de fase da base (`scripts/validate_phase.py`): lock com hashes, ruff, mypy strict, import-linter, testes, cobertura de domínio, gitleaks, pip-audit, semgrep, SBOM e demo.

| Camada | O que prova |
|---|---|
| **Unit** | Regras do domínio, com propriedades (`hypothesis`) em redação, política e máquina de estados |
| **Contrato** | Kit de módulo, porta `Tracker` e `Agent`, snapshot da telemetria |
| **Arquitetura** | Camadas, módulo não importa módulo, nomes proibidos, limite de linhas, piso sem flag |
| **Integração** | Git real, SQLite, coletor em processo |
| **E2E com simulador** | Fluxo completo sem gastar tokens (`fake.py`) |
| **E2E real** | `nexo selftest --agent <ferramenta>` em projeto descartável |
| **Portabilidade** | Proibição de `shell=True`, `sh`, `/tmp` e caminho montado com texto; regras de `ntpath` para separadores e caminhos longos; fixtures com CRLF, BOM e nomes que só diferem por maiúscula; adaptadores de agendador e cofre testados por contrato com simuladores |
| **Harness** | Casos do toolkit: handoff desatualizado, parada e retomada, bloqueio parcial, revisão somente leitura |

**Quando roda:** a validação rápida num `pre-push` opcional do repositório do Nexo; a completa antes de cada versão, com as verificações de portabilidade abaixo. **Os testes finais rodam no Windows** (fase MF), e nenhuma versão é liberada às pessoas sem eles. Linux é melhor esforço. A prova fica em `evidence/<versão>/<sistema>.json`. **Testes reais rodam só antes da versão**, por custo. Os workflows da base (`deploy/ci/*`, `.github/workflows/*`) **não são copiados**.

**Evidência:** "instalado", "configurado" e "funcionando" são três afirmações diferentes. Nenhuma tabela de maturidade deste documento vira 🟢 sem o comando, a versão e o resultado registrados.

---

## 21. Riscos e pendências

### 21.1 Riscos

| ID | Risco | Impacto | O que fazer |
|---|---|---|---|
| **R-02** | Marca "Nexo": já é um nome de produto seu descontinuado e é comum no mercado. Não fiz busca de marca. | Médio se virar público | Pesquisar antes de publicar. Uso privado não é afetado. |
| **R-03** | Tamanho do MVP, com 3 ferramentas e o painel completo. | **Alto** | Primeiro uso útil na M5. Se apertar, corte nesta ordem: Copilot, DORA e ROI, depois Ferramentas e Frota. **FinOps, Squads e Board são o motivo do central e ficam.** |
| **R-04** | Herdar complexidade: a base tem muito recurso sem uso real. | Alto | Só copiar o que está no MVP (§4.4). O resto fica em módulo futuro. |
| **R-05** | Mudanças de formato nas CLIs e nos arquivos locais de sessão, sem contrato público. | Médio | Monitor sintético, `verified` por versão. Falha do leitor não trava a execução. |
| **R-06** | PM e QA podem nunca usar um terminal. | Médio | Teste de uso no critério do MVP. Se falhar, priorizar aprovar e responder pelo painel web. |
| **R-07** | Upstream muda e o pack fica velho. | Baixo | Lock explícito. `nexo pack diff` mostra o quanto divergiu. Atualização sempre é uma decisão. |
| **R-08** | Grafo vira esforço sem retorno. | Baixo | Só a preparação no MVP. Implementar depois de medir falha real de busca. |
| **R-09** | Automatizar `fetch` e `push` no repositório do time pode gerar disputa e ruído com várias pessoas gravando juntas. **É a parte mais nova e sem base na base.** | Médio | Um arquivo por evento e por seção, refazer em caso de disputa, e prova com duas máquinas na M2. |
| **R-10** | Propostas demais transformam o refinamento em burocracia. | Médio | Só mudança em critério bloqueia. Medir propostas por história e ajustar. |
| **R-11** | O cálculo de impacto errar para menos: uma tarefa afetada continua rodando e ninguém percebe. | Alto | Na dúvida, pausa tudo. A validação final contra a versão nova cobre o que escapar. Comparar o previsto com o refeito de verdade, antes de confiar nele. |
| **R-12** | Premissa provisória virar hábito para contornar o PM. | Médio | Merge retido, proibida em área sensível, e o painel mostra quantas foram usadas e recusadas. |
| **R-13** | **Controles técnicos da empresa ainda podem barrar.** A liberação está dada, mas EDR, AppLocker, proxy ou Segurança podem bloquear agente e executável, e isso só aparece no Windows corporativo. | Médio | Grupo B do §25.3 e R-21. |
| **R-14** | Falso conforto: a linhagem parecer completa quando a maioria dos times não publica. | Alto | Estados de completude, cobertura visível e a regra "consumidores conhecidos". A incerteza nunca libera nada no portão. |
| **R-15** | Baixa adesão de outros times. | Médio | Nível 1 é um YAML, e o nível 2 não altera repositório nenhum. A descoberta pela infraestrutura cobre parte do que faltar. |
| **R-17** | Linhagem desatualizada: depende de alguém do time ter o repositório clonado e a máquina ligada. Um repositório que ninguém clona não atualiza. | Médio | Estado `obsoleto` e `nexo lineage coverage`, que lista os repositórios de `lineage.repos` sem ninguém publicando. |
| **R-18** | A empresa pode não permitir criar o repositório do time, ou deixá-lo legível para toda a organização. | **Alto** | Conferir na P0. Alternativa: S3, pelas portas `TrackingStore` e `LineageStore`. Sem isso, o fluxo entre pessoas não funciona. |
| **R-19** | **Sem CI, nenhum servidor impõe o portão.** Quem abrir um PR fora do Nexo não passa por ele, e a evidência local pode ser forjada por quem tiver a máquina. | **Alto** | O PR mostra evidência ou a falta dela, e o revisor confere com `nexo tl review`. Só o pipeline predefinido e a proteção de branch são impostos pelo servidor. O Nexo não pode prometer controle obrigatório (§24.2). |
| **R-20** | Máquinas corporativas podem bloquear o agendador, os hooks, o executável sem assinatura ou o proxy. | Alto | Conferir na P0, porque você acredita que é permitido, mas ainda não foi testado. O scan também dispara ao usar o Nexo. `nexo doctor` diz o que está ativo. |
| **R-21** | **Risco principal de plataforma: o destino é Windows, e ele só é testado no fim.** Um bloqueio de EDR ou um defeito de Windows pode aparecer depois de o produto estar pronto, com mais retrabalho. | **Alto** | Regras e testes de portabilidade desde a M0 (§20). **Recomendado:** rodar o grupo B, de poucas horas, sem esperar a MF. |
| **R-22** | Copilot sem guarda comprovada pode não poder receber o código da empresa, e os leitores de consumo nunca rodaram no Windows. O FinOps ficaria incompleto. | **Alto** | Provar na P0 e na M3. Até lá, o painel marca a ferramenta como não medida. |
| **R-23** | Painel virar avaliação individual de pessoas. | **Alto** | Pseudônimo, matriz no servidor e sem ranking (§15.3). |
| **R-24** | Número errado no painel de FinOps minar a confiança. Preço de referência parece gasto real. | **Alto** | Rótulo "estimado", conciliação com a fatura e prova na M7. |
| **R-26** | Login provisório virar permanente: o portal controla flags e mostra custo. | Alto | Só rede interna, token por pessoa com validade, perfil `admin` restrito. Prazo para o SSO em Q-24. |
| **R-25** | Time do Nexo pequeno operando serviço em produção, além de desenvolver. | Médio | Serviço gerenciado (Fargate e RDS), alarmes e o princípio de que queda do central não trava ninguém. |
| **R-16** | Scanner por padrão de texto errar em código real: constantes, herança, encapsulamento em bibliotecas internas. | Médio | Evidência em cada ligação, "não resolvido" em vez de chute e medição de acertos na M8. Se o resultado for ruim, usar análise com Roslyn. |

### 21.2 Perguntas que ainda ficam abertas

Nenhuma bloqueia a M0. O padrão vale até você decidir.

| ID | Pergunta | Padrão assumido | Precisa até |
|---|---|---|---|
| Q-03 | Quais são os 2 projetos reais da prova na M5? | Escolha sua. Sugestão: um Python e um React | M5 |
| Q-04 | Quando o Nexo migra para a organização da empresa, e em qual delas ficam o cérebro e os repositórios de time? | Antes do primeiro uso com dados da empresa, que começa na fase MF | Antes da MF |
| Q-06 | Quando entrarem outras pessoas, será `team` com qual identidade? | GitHub | Primeiro usuário adicional |
| Q-07 | Os papéis obrigatórios por tipo de reunião estão certos? | Funcional: PM + TL. Técnica: TL + um dev | M2 |
| Q-08 | Depois que o dev melhora o refinamento, o TL precisa revisar mesmo sem proposta sua? | Não: revisa só o que muda seção dele | M2 |
| Q-10 | Os times usam só `Confluent.Kafka` e AWS SDK, ou também MassTransit, KafkaFlow ou um wrapper interno? | Só `Confluent.Kafka` e AWS SDK | Início da M8 |
| Q-11 | A infraestrutura é só Terraform, ou também CDK, SAM ou CloudFormation? | Só Terraform | Início da M8 |
| Q-12 | Quem pode rodar a descoberta na AWS e no Kafka? | Fica desligada até haver um responsável | Quando for ligar `lineage.discover` |
| Q-16 | As máquinas permitem agendador de usuário e hooks do Git? Você acredita que sim | Sim; confirmar com teste na P0 | P0 |
| Q-24 | Qual é o provedor de SSO (Entra ID, Okta...), expõe OIDC, envia grupos e quando será conectado? | OIDC; data a definir | Antes de conectar |
| Q-21 | Qual conta AWS e rede usar, e as máquinas alcançam a rede interna sem VPN? O padrão de implantação atende um serviço novo? O dono já é o time do Nexo | A definir com a plataforma | Antes da M7 |
| Q-22 | A chave de assinatura fica no AWS KMS? | Sim: chave ECC P-256, com permissão `kms:Sign` só para os responsáveis do time do Nexo. Novo adaptador, seguindo o de Azure Key Vault da base | Antes da M6 |
| Q-23 | O pipeline predefinido registra implantações no GitHub (Deployments ou releases)? | Se não, frequência de implantação fica `sem dado` | Antes da M7 |
| Q-17 | A empresa permite criar `nexo-<time>`, interno e legível por toda a organização, e sem workflow forçado? | Sim; confirmar na P0. Alternativa: S3 | P0 |

---

## 22. Glossário

| Termo | Significado |
|---|---|
| **Módulo** | Pasta com manifesto que oferece comandos, flags e portões, sem conhecer outros módulos |
| **Kernel** / **piso** | O que nunca desliga: segurança, auditoria, portões |
| **Pack** | Conteúdo de terceiro adotado com commit fixado, hash e licença |
| **Portão** | Verificação determinística que deixa passar, leva a uma pessoa ou reprova |
| **Portão duro** | O que nenhuma nota nem aprovação supera |
| **DoR** | Definition of Ready, o que precisa existir antes do desenvolvimento |
| **Demanda** | Qualquer unidade de trabalho rastreada, humana ou gerada pela execução |
| **Etapa** | Onde a demanda está no fluxo. Define com quem ela está |
| **Holder** | Pessoa ou papel que está com a demanda agora. Calculado, nunca editado |
| **Seção** | Parte do refinamento com um dono (funcional, técnica, testes, implementação) |
| **Proposta** | Mudança sugerida na seção de outro papel, que só entra se o dono aceitar |
| **Lock** | Cópia congelada e versionada da spec aprovada, guardada no branch da história |
| **Premissa provisória** | Decisão temporária do TL que libera a continuação e segura o merge até o dono decidir |
| **Impacto** | Tarefas e testes ligados ao que mudou na spec, calculados sem IA |
| **Experimento** | Execução de `nexo try`, sem demanda |
| **Rota** | Combinação de ferramenta e modelo |
| **Handoff** | Pacote de contexto para outra rota continuar o trabalho |
| **Cérebro** / **brain** | Repositório Git assinado de conhecimento e padrões |
| **Coletor local** | Leitor do consumo registrado pelas CLIs |
| **Central** | Serviço que consolida telemetria, flags e dados do painel |
| **Portal** | Site do Nexo: gestão de features, dashboard, insights e board |
| **Linhagem** | De onde vem e para onde vai cada componente, passando pelos canais |
| **Canal** | Tópico Kafka, tópico SNS, fila SQS ou API HTTP que liga componentes |
| **Manifesto de linhagem** | Arquivo gerado em cada repositório com seus componentes e canais, só metadados |
| **Repositório do time** | `nexo-<time>`, na organização: demandas, refinamento, pessoas e linhagem do time |
| **Consumidores conhecidos** | Os que estão registrados. Não significa que sejam todos |

---

## 23. Linhagem de componentes

**Objetivo:** responder, sem depender de quem lembra, "de onde vem e para onde vai" cada componente, inclusive entre repositórios e times diferentes. Por exemplo: a gestão de posição recebe mensagens do tradutor.

**Nível:** componente → canal → mensagem. **Linhagem por campo está fora do escopo** (D-25).

**Maturidade:** tudo aqui é 🔵. A base tem algo mais simples: o `produto.yaml` declara partes e usos, e o portal compara com o código. Mas ele só detecta rotas HTTP e citações ao nome de outra parte, então **não enxerga mensageria**, onde produtor e consumidor não se citam.

### 23.1 Modelo

```text
team-a/tradutor ──publica──► kafka:posicao.traduzida ──consome──► team-b/gestao-posicao
    (ecs)                          │                                    (ecs)
                            contrato: PosicaoTraduzida v3
```

| Nó | O que é | Identidade |
|---|---|---|
| **Componente** | O que é implantado: serviço ECS, Lambda, worker | `<time>/<componente>` |
| **Canal** | Tópico Kafka, tópico SNS, fila SQS, API HTTP | `kafka:<nome>`, `sns:<nome>`, `sqs:<nome>`, `http:<serviço>` |
| **Mensagem** | Contrato que passa pelo canal, só nome e versão | `<nome>@<versão>` |
| **Repositório** | Onde o código está | `github:<org>/<repo>` |

**Componente não é repositório.** Um repositório pode ter vários componentes, e cada componente aponta para o repositório onde está. O elo entre repositórios é o **canal**, não o nome do outro serviço.

**Arestas:** `publishes`, `consumes`, `subscribes` (SNS→SQS), `triggers` (fila ou tópico→Lambda), `calls` (HTTP), `lives_in` e `owned_by`.

**Normalização do nome do canal** (`lineage.channel_rules`, porque a convenção é definida por cada organização):

- tira o ambiente: `hom-posicao.traduzida` e `prd-posicao.traduzida` viram um só canal;
- agrupa retentativa e erro: `<tópico>.retry.<n>` e `<tópico>.dlt` ficam ligados ao tópico principal;
- tira a conta e a região dos ARNs: o manifesto nunca leva o ID da conta AWS.

### 23.2 Como é extraído

**Sem IA, sem CI e sem máquina dedicada.** Roda sozinho na máquina de cada pessoa do time, sem ela precisar lembrar (D-26). Foco inicial em .NET, Kafka, SQS, SNS, ECS e Lambda.

**O scan sempre lê `origin/main`, nunca a pasta de trabalho**, sem checkout, por `git fetch` e leitura direta dos objetos. Assim, uma alteração local ou um branch em andamento não aparece como linhagem, e o trabalho da pessoa não é tocado. O mesmo commit, com a mesma versão do scanner, gera sempre o mesmo manifesto.

| Gatilho | Quando dispara | Precisa de |
|---|---|---|
| **Uso normal do Nexo** | Ao abrir o console, pelo piloto automático da base, e depois de `dev pr` e `pr sync`, em segundo plano | Nada, é o padrão |
| **Hook local do Git** (`post-merge`) | Depois de um `git pull` na `main`. Em segundo plano, nunca bloqueia nem falha o Git | Instalado por `nexo init` ou `nexo join`, no repositório, preservando hooks existentes |
| **Agente local** (`nexo lineage agent`) | Tarefa de usuário a cada 30 minutos, com a máquina ligada (`launchd`, `systemd --user`, Agendador de Tarefas). Não fica residente: roda e termina | Instalado uma vez, com confirmação, por `nexo init` ou `nexo join`. `nexo lineage agent uninstall` remove |

**O agente é o que torna a linhagem automática:** ele atualiza mesmo se a pessoa só fez merge pela página do GitHub e não abriu o Nexo naquele dia. Sem ele, ou se a máquina bloquear o agendador, o dado só atualiza com os outros gatilhos.

#### Como cada máquina trabalha

1. Lista os repositórios do time clonados naquela máquina (`~/.config/nexo/projects.yaml`).
2. Compara a `main` remota com o último commit já publicado, por `git ls-remote`. Se nada mudou, termina sem custo.
3. Baixa o que faltar e reexamina **só os arquivos alterados**, com cache por arquivo, em prioridade baixa.
4. Valida, publica em `nexo-<time>/lineage/` e termina.

| Tema | Regra |
|---|---|
| **Credenciais** | **Usa o Git da própria pessoa, sem token compartilhado.** Só alcança o que ela já pode ler. `GIT_TERMINAL_PROMPT=0`: nunca pede senha em segundo plano |
| **Várias máquinas** | Muitas podem tentar publicar o mesmo commit. Antes de gravar, o Nexo busca o repositório do time: se o commit já está lá, não faz nada. Disputa no `push` é refeita sobre a versão nova |
| **Máquina atrasada** | Só publica se o seu commit for **mais novo** (`git merge-base --is-ancestor`). Nunca sobrescreve um manifesto mais novo com um antigo |
| **Versões diferentes do Nexo** | O manifesto registra a versão do scanner. Para o mesmo commit, a versão maior vence, e uma versão menor nunca sobrescreve |
| **Sem internet ou VPN** | Tenta de novo depois, sem aviso na hora. `nexo doctor` mostra a última execução e o último erro |
| **Sem escrita no repositório do time** | Lê normalmente e não publica. O `doctor` explica |
| **PM e QA** | Sem repositório de código clonado, nada é escaneado. Eles só leem |

**O que isso não cobre:** repositório que ninguém do time tem clonado, ou máquinas todas desligadas. O dado fica atrasado até alguém voltar, e `nexo lineage coverage` mostra isso (§23.6).

| Fonte | O que procura | Confiança |
|---|---|---|
| **Terraform** (`aws_sns_topic_subscription`, `aws_lambda_event_source_mapping`, `aws_ecs_service`, `aws_sqs_queue`, `aws_lambda_function`) | Componentes, assinaturas e gatilhos | Alta |
| **Contratos** (AsyncAPI, OpenAPI, Avro) | Canal, mensagem, versão | Alta |
| **Configuração** (`appsettings*.json`, variáveis da task definition) | Nome do tópico, da fila ou da URL | Média |
| **Código C#:** Kafka (`ProducerBuilder`, `ProduceAsync`, `ConsumerBuilder`, `.Subscribe`), AWS (`PublishAsync`, `SendMessageAsync`, `ReceiveMessageAsync`), Lambda (`SQSEvent`, `SNSEvent`, `KafkaEvent`), HTTP (`[HttpGet]`, `MapGet`) | Direção: quem publica e quem consome | Média |
| **Declaração** em `.nexo/component.yaml` | O que os scanners não alcançam | Declarada |
| **AWS e Kafka, somente leitura** (§23.5) | O que existe de fato, inclusive de outros times | Alta |

| Regra | Comportamento |
|---|---|
| **Evidência em toda ligação** | Fonte, arquivo, linha, commit, data e confiança |
| **Sem adivinhar** | Nome montado em tempo de execução vira **"não resolvido"**. A IA pode sugerir, mas só vale depois de uma pessoa confirmar em `nexo lineage confirm` |
| **Resolve indireção simples** | Constante em C# e chave de `IConfiguration` são procuradas no `appsettings`. O que não resolver segue a regra acima |
| **Sobreposição** | Declaração nunca apaga o que foi detectado. Se divergirem, vira alerta |
| **Bibliotecas** | Primeiro `Confluent.Kafka` e AWS SDK, padrão das skills de fábrica. MassTransit e KafkaFlow só se Q-10 confirmar o uso |

### 23.3 O manifesto de cada repositório

```yaml
# gerado por nexo lineage scan; só metadados
schema: nexo.lineage/v1
team: time-posicao
repo: github:minha-org/gestao-posicao
commit: 3f2a91c
generated_at: 2026-11-03T10:12:00-03:00
components:
  - id: time-posicao/gestao-posicao
    runtime: ecs
    consumes:
      - channel: kafka:posicao.traduzida
        message: PosicaoTraduzida@3
        evidence: { source: code, file: src/Worker/Consumer.cs, line: 42, confidence: medium }
    publishes:
      - channel: sns:posicao.atualizada
        evidence: { source: terraform, file: infra/sns.tf, line: 8, confidence: high }
unresolved:
  - { file: src/Worker/Dynamic.cs, line: 77, reason: "tópico montado em tempo de execução" }
```

**Nunca entram:** código, payload, valores de configuração, segredos, conta AWS, nomes de pessoas. Um teste de contrato garante isso, e o manifesto passa pela redação.

### 23.4 Sincronização entre times

**Como os repositórios ficam em lugares diferentes e cada time só lê os próprios, a sincronização não pode depender de ler o código dos outros.** Cada time **publica o próprio manifesto** na pasta `lineage/` do seu repositório `nexo-<time>` (§14.3). Esse repositório é legível pela organização. O código de ninguém é compartilhado.

```text
 Repo de código do time A ─┐
 Repo de código do time A ─┼─ máquinas do time A ─► nexo-time-a/lineage/ ─┐
 Repo de código do time B ─── máquinas do time B ─► nexo-time-b/lineage/ ─┼─► cada leitor consolida
 Time C, sem Nexo ──────────── YAML à mão ────────► nexo-time-c/lineage/ ─┘   nexo lineage · Obsidian
```

**Por que um repositório por time:** a própria permissão do GitHub garante que só o time grava nele, sem CI, sem proteção de branch e sem pasta compartilhada que outro time poderia alterar. Também evita depender de um repositório central com dono indefinido. Decisão D-24.

**Como os times se encontram:** o Nexo lista os repositórios da organização com o tópico `nexo-team`, pela API do GitHub. Se a API ou o tópico não estiverem disponíveis, usa a lista `lineage.teams`. **Requisito: os repositórios do time precisam ser legíveis por toda a organização** (Q-17), senão aquele time fica invisível.

O acesso fica atrás de uma porta (`LineageStore`), que também aceita um bucket S3 com permissão por prefixo, se o Git não servir.

| Regra | Comportamento |
|---|---|
| **Quem grava** | Só quem tem escrita no repositório do time |
| **Ninguém fala por outro time** | Um time pode dizer "eu consumo X", nunca "o time B publica X". Sobre outro time, só com status `claimed` |
| **Validação sem CI** | Roda em dois pontos: **antes de publicar** e **de novo ao ler**. Quem lê não confia no que encontra: manifesto inválido, com campo proibido ou com `team` diferente do repositório é ignorado e aparece em `nexo lineage coverage` |
| **Consolidação sem CI** | **Não há arquivo consolidado.** Cada leitor busca os repositórios dos times, junta os manifestos pelo ID normalizado do canal, sem IA, e guarda em cache local. Como é determinístico, todos chegam ao mesmo resultado |
| **Quem publica** | A máquina de qualquer pessoa do time, com as próprias credenciais Git |
| **Fora da assinatura do cérebro** | A linhagem é dado derivado e muda a cada merge. Passar por PR manual e assinatura no cérebro seria inviável |
| **Exportação** | A consolidação também pode ser exportada para catálogos como o Backstage, se a organização usar (`catalog-info.yaml`), depois do MVP |

**Como um time adota, do menor ao maior esforço:**

| Nível | O que faz | O que os outros veem |
|---|---|---|
| **0: nada** | Nada | Aparece só pelo que os outros dizem e pela AWS, como `não confirmado` |
| **1: à mão** | Um repositório `nexo-<time>` com um YAML curto em `lineage/` | Componentes e canais declarados |
| **2: só o agente** | As pessoas do time instalam o Nexo e ligam só a linhagem (`nexo lineage agent install`, com os outros módulos desligados). Nada muda nos repositórios, exceto o hook local, que não é versionado | Tudo o que o scan encontra, conforme as máquinas estejam ligadas |
| **3: Nexo completo** | Usa o fluxo por papéis | O acima, mais avisos entre times integrados à fila dele |

### 23.5 Descoberta pela AWS e pelo Kafka

Para os times que não publicam, a infraestrutura é a única fonte que não depende deles. **É opcional, somente leitura e nunca automática:** por exigir permissão na conta, roda só por comando de quem tiver essa permissão, na máquina dela. As credenciais de nuvem nunca entram no agente local.

| Comando | O que traz |
|---|---|
| `nexo lineage discover aws` | Assinaturas SNS→SQS, gatilhos de Lambda, serviços ECS e tags de time |
| `nexo lineage discover kafka` | Tópicos, grupos de consumidores e ACLs. Com schema registry, o nome e a versão dos contratos |

Grupo de consumidor e ACL mostram **que alguém consome**, mesmo sem saber o repositório. Vira nó `não identificado`, com o nome do grupo, até o time dono se apresentar. A descoberta grava em `lineage/discovery/` no repositório do time que a rodou, com o mesmo formato de manifesto e sem o ID da conta.

### 23.6 Completude

O sistema nunca finge estar completo. Cada canal tem um estado:

| Estado | Significado |
|---|---|
| `confirmado` | Os dois lados publicaram manifesto |
| `parcial` | Só um lado publicou, por exemplo, publicador desconhecido |
| `não confirmado` | Veio só de um terceiro ou da AWS |
| `não identificado` | Há consumo na infraestrutura, mas sem componente ligado |
| `obsoleto` | Manifesto de um commit anterior ao atual da `main`, quando o leitor tem acesso ao repositório. Sem acesso, manifesto mais antigo que `lineage.stale_days`, padrão de 30 dias |
| `desvio` | Ligação vista no código que o `produto.yaml` não declara |

```text
$ nexo lineage show time-posicao/gestao-posicao
ENTRADAS
  kafka:posicao.traduzida   ◄ time-traducao/tradutor       confirmado   PosicaoTraduzida@3
  sqs:posicao.correcoes     ◄ ?                            parcial      ninguém publicou
SAÍDAS
  sns:posicao.atualizada    ► time-risco/calculo-risco     confirmado
                            ► grupo "relatorios-prd"       não identificado

Cobertura: 3 de 5 ligações confirmadas · 1 manifesto com 41 dias
```

**Regra de linguagem:** o Nexo diz **"consumidores conhecidos"**, nunca "sem consumidores". Ausência de registro não prova ausência de uso, e isso vale para toda decisão tomada com base na linhagem.

### 23.7 Uso no fluxo

```bash
nexo lineage show <componente>
nexo lineage upstream|downstream <componente> [--depth N]
nexo lineage impact <canal|mensagem>      # quem pode ser afetado
nexo lineage coverage [--team]            # o que falta para ficar completo
nexo lineage scan · publish · validate · confirm
nexo lineage agent install|status|run|uninstall   # gatilho automático na máquina
nexo lineage export --obsidian <pasta>    # notas com [[links]], fora do Git
```

| Onde | O que acontece |
|---|---|
| **`nexo tl analyze`** | Mostra sozinho quem consome o que a feature altera, com o estado de cada ligação |
| **Portão** | Contrato com mudança incompatível **sempre vai para decisão humana**, mesmo sem consumidor conhecido. Isso não bloqueia sozinho: a cobertura pode estar incompleta |
| **PR** | Se a linhagem mudou, o diff aparece no PR ("agora publica em X") |
| **Desvio** | Ligação nova não declarada vira alerta para o TL |
| **Agente** | `nexo-mcp: lineage_neighbors` entrega só nomes, como dado, com o cercamento de conteúdo externo |

**Demanda em outro time.** O Nexo não pode gravar no repositório de quem você não tem acesso. Por isso:

1. Mudar o tradutor com consumidores conhecidos abre uma issue no repositório `nexo-<consumidor>`, pela API do GitHub. O time é avisado como em qualquer issue. Leitura basta para abrir a issue, então não é preciso ter escrita lá.
2. No seu board, a demanda fica ligada e marcada como `aguardando time-risco`.
3. Se o outro time usar o Nexo, a issue entra na fila dele, em triagem.

### 23.8 Cérebro e Obsidian

| O quê | Onde fica | Por quê |
|---|---|---|
| **Fatos gerados** | Repositório de cada time + cache local | Mudam a cada merge e são publicados pelas máquinas do time |
| **Decisões humanas** ("por que o tradutor existe") | Cérebro, por PR, em `applies_to: component:<id>` | Conhecimento curado |
| **Visão no Obsidian** | `nexo lineage export --obsidian` numa pasta ignorada pelo Git | O grafo aparece no Obsidian sem entrar na assinatura, que usa `git ls-files` (§13.5) |

**Exceção ao isolamento (D-06):** o contexto entregue ao agente pode trazer **nomes** de componentes, canais e contratos de outros projetos. Nada além disso. Projeto `restricted` não publica nem recebe.

**Grafo:** o formato de nós e arestas é o mesmo de `nexo brain index` (§13.4), para que o grafo futuro una código, cérebro e linhagem.

### 23.9 Módulo e flags

Módulo `lineage`, com `requires: []` e `optional: [demands, central-client]`. Flags: `lineage.scan` (beta), `lineage.publish` (beta), `lineage.discover` (experimental), `lineage.gate` (experimental). Sem publicar, tudo funciona com os repositórios locais do próprio time (`nexo lineage scan --repos <pasta>`).

## 24. Sem GitHub Actions

**Restrição:** os workflows seguem um padrão predefinido, e não é possível criar nem apagar tarefas no Actions. **Nenhuma parte do Nexo conta com isso** (D-27). O que já existe continua existindo, e o Nexo só lê o resultado.

### 24.1 O que muda

| Item | Antes | Agora |
|---|---|---|
| `quality ci` da base | Gerava workflow | **Não existe no Nexo** |
| Workflows prontos (`deploy/ci/*`) | Copiados | **Não são copiados** |
| `nexo tl setup` | Criava CI | Lê os comandos do pipeline existente com `codemap.pipeline_commands`, para rodar localmente o mesmo que o pipeline roda |
| `nexo dev pr sync` | Reagia ao CI do Nexo | Lê o resultado do pipeline predefinido, sem alterar nada. Se não houver, vale só o portão local |
| Portal e DORA do pipeline (depois do MVP) | Rodavam em CI | Serão redesenhados, se entrarem |
| `brain lint`, merge de rascunho | No PR, por CI | No cliente, com bloqueio na assinatura. Sem merge automático (§13.5) |
| Linhagem | CI em cada repositório | Máquina de cada pessoa (§23.2) |
| Validação do próprio Nexo | CI em três sistemas | `scripts/validate.py` local (§20) |

**Regra determinística nova 🔵:** a política do agente proíbe escrever em `.github/workflows/**`, e o portão reprova um diff que altere esses arquivos, mesmo vindo de uma pessoa, dentro do fluxo do Nexo. Assim, nem a IA nem o `pack` tentam criar uma tarefa que o padrão não permite. A skill `esteira-ci-cd` e os prompts do toolkit que falam em criar CI são ajustados ou rejeitados no lock do pack.

### 24.2 O que se perde

**Sem CI próprio, nenhum servidor impõe os portões do Nexo. Eles valem para quem usa o Nexo.** Antes, o CI podia repetir a verificação independentemente da máquina de quem fez a mudança. Isso não dá para substituir por completo, e a spec não promete o contrário (R-19).

| O que reduz o risco | Como funciona | Limite |
|---|---|---|
| **Evidência no PR** | `nexo dev pr` publica no PR, como comentário, o resultado do portão, com commit e hash da spec aprovada | Quem controla a máquina pode forjá-la |
| **Conferência pelo revisor** | `nexo tl review <pr>` refaz o pipeline local e a rastreabilidade na máquina do revisor. Sem evidência, o PR aparece marcado | É um ato humano, não imposto pelo GitHub |
| **Status no commit, opcional** | O Nexo pode gravar um status `nexo/gate` pela API, sem Actions. Se a organização permitir, a proteção de branch pode exigi-lo | Precisa de permissão da organização; é só um sinal, não uma prova |
| **O que continua imposto pelo servidor** | Pipeline predefinido e proteção de branch | Só o que a organização já definiu |

Consequência no controle: a **trilha de auditoria continua local** e mostra o que passou pelo Nexo, mas não pode provar que nada foi feito fora dele.

## 25. Ambiente corporativo e Windows

### 25.1 Autorização antes de tudo

**O Nexo será da empresa, e os fornecedores aprovados são Claude, Devin e Copilot. O responsável liberou o uso na empresa.** A liberação fica registrada em `org/approval.yaml` do cérebro (quem, quando e com qual escopo), e `nexo doctor` a mostra.

| Tema | O que precisa ser resolvido | Quem normalmente decide |
|---|---|---|
| **Instalação** | Nexo, agente agendado e hooks nas máquinas | TI, Segurança |
| **Código para IA** | Claude, Devin e Copilot estão aprovados. `data_policy` nega o que não estiver listado | Segurança |
| **Transcrição de reuniões** | Gravações têm dados pessoais e estratégicos. O padrão é transcrição local, com aviso aos participantes | Segurança |
| **Dados na organização** | Cérebro, repositório do time e central ficam na empresa. **Nenhuma cópia em conta pessoal** | Segurança |
| **Propriedade do Nexo** | **Será da empresa.** A construção começa na máquina e no repositório privado do responsável e migra para a organização depois (D-33). **Até a migração, o repositório não recebe código, dados ou conhecimento da empresa** | Responsável |
| **Conteúdo de terceiros** | Licenças MIT do toolkit e do Superpowers mantidas em `NOTICE`; dependências revisadas | Time do Nexo |

**Regra:** antes da M1, registrar a liberação. As máquinas continuam sujeitas aos controles técnicos da empresa.

### 25.2 Windows como plataforma principal

**Ponto de partida:** o CI da base passava nos três sistemas. Mas o fluxo real no Windows **não foi validado**: faltam `selftest` com as CLIs e a leitura de consumo do Copilot e do Devin.

| Tema | Regra |
|---|---|
| **Instalação** | Binário `onedir` do PyInstaller, sem exigir Python na máquina. PM e QA instalam sem privilégio de administrador, por usuário |
| **Assinatura do executável** | Executável sem assinatura costuma ser bloqueado por EDR ou AppLocker. Definir na P0 se será preciso certificado da empresa |
| **Agendador** | `schtasks` no nível do usuário, sem administrador. Só com a sessão aberta |
| **Hooks do Git** | Rodam no `sh` do Git for Windows. O hook é só uma linha que chama `nexo.exe` em segundo plano |
| **Caminhos longos** | Repositórios .NET têm caminhos profundos. Worktrees ficam em `%LOCALAPPDATA%\nexo\wt\<hash>`, com `core.longpaths=true` por comando |
| **Fim de linha** | Hash de seção, manifesto e lock sempre em LF. O scan lê os objetos do Git, sem conversão. Sem isso, Windows e macOS teriam hashes diferentes, e as aprovações pareceriam alteradas |
| **Separadores** | Caminhos internos e manifestos sempre com `/`. O canal é normalizado da mesma forma em qualquer sistema |
| **Maiúsculas** | Sistema de arquivos não diferencia. Nomes que só diferem por maiúscula são erro no `brain lint` e no repositório do time |
| **Arquivos travados** | Antivírus e editores travam arquivos. Toda remoção de worktree tenta novamente com espera |
| **Comandos** | Sem `sh` em lugar nenhum do fluxo. Executáveis `.cmd` das CLIs de IA, como já tratado na base |
| **Terminal** | UTF-8 forçado na saída. Sem depender de cores ou caracteres fora do que o Windows Terminal e o `cmd` exibem |
| **Credenciais** | Windows Credential Manager, pelo `keyring`. Git pelo Git Credential Manager |
| **Proxy e TLS** | Usa proxy do sistema e o repositório de certificados do Windows. Inspeção de TLS é comum em empresas e quebra `pip` e chamadas HTTP |
| **Obsidian** | Funciona no Windows. O vault é um clone normal |

Todas as regras acima valem desde a M0, e os adaptadores de Windows (`schtasks`, Credential Manager, hook) só são provados de verdade na fase MF. **Linux** é melhor esforço.

### 25.3 Prova do ambiente (fase P0)

**Grupo A, na P0:** itens 1, 2, 3, 5, 9, 9a e 9b. **Grupo B, no Windows:** itens 4, 6, 7, 8 e 10, além de repetir 5, 9 e 9a (R-21).

| # | Teste | Se falhar |
|---|---|---|
| 1 | Criar `nexo-teste`, interno, só com `main`, legível pela organização | Usar S3 ou redesenhar (§14.3) |
| 2 | Push nele não dispara workflow forçado | Conversar com a plataforma, porque há custo e ruído |
| 3 | Criar um branch no padrão da empresa e abrir PR pela API | Ajustar `git.branch_pattern` |
| 4 | Criar tarefa de usuário em `schtasks`, sem administrador | Linhagem só pelo uso do Nexo |
| 5 | Hook `post-merge` local executa | Linhagem sem o gatilho do hook |
| 6 | Rodar um `.exe` de teste do PyInstaller, com e sem assinatura | Definir certificado ou outra distribuição |
| 7 | `keyring` grava e lê no Credential Manager | Definir outro cofre |
| 8 | Chamada HTTPS à API do GitHub e aos fornecedores, passando pelo proxy | Configurar proxy e certificados |
| 9 | Claude, Devin e Copilot rodam de forma não interativa e registram consumo em disco | Tirar a ferramenta do MVP ou ajustar o leitor |
| 9a | Hook bloqueia uma ferramenta proibida no Devin e no Copilot, na versão atual | Limitar a ferramenta às classes de dados permitidas (§11.1) |
| 9b | Um contêiner de teste sobe pelo padrão de implantação da empresa | Definir com o time de plataforma (Q-21) |
| 10 | Caminho com mais de 260 caracteres numa worktree de repositório .NET real | Encurtar o caminho |

O resultado fica em `evidence/p0/`. É a fase mais barata e a que mais pode evitar retrabalho.

## 26. Portal web

**Objetivo:** um site da empresa para gerir as features do Nexo e acompanhar dashboard e insights, com o SSO conectado depois, sem refazer nada.

**Decisão:** é o módulo `portal`, servido junto com o central, porque usa os mesmos dados, a mesma matriz de visibilidade e a mesma implantação. A base já tem a maior parte: páginas, drill, insights com estado, alteração de flags e login OIDC (`collector/app/web.py`, `dashboard.py`, `metrics/`). Tudo é 🟡: existe e tem testes, mas não encontrei registro de operação real.

Mantém a regra do painel atual: HTML servido pelo servidor, sem JavaScript obrigatório e sem CDN, o que evita problemas com proxy e política de segurança da empresa. A marca vem de `brand.yaml`.

### 26.1 Telas

| Tela | O que faz | Quem acessa | Estado |
|---|---|---|---|
| **Início** | O que precisa de atenção agora, por perfil | Todos | 🟡 |
| **Features** | Gestão de módulos e flags (§26.2) | Time do Nexo, admin; TL só nas flags `squad_managed` | 🔵 sobre 🟡 |
| **Dashboard** | Executiva, FinOps, Squads, Qualidade, Ferramentas, DORA e Frota (§15.1) | Matriz de visibilidade | 🟡 |
| **Insights** | Lista por estado, evidência, tratar ou descartar com motivo | Todos, conforme a matriz | 🟡 |
| **Insights: configuração** | Liga cada detector e ajusta limiares | Time do Nexo, admin | 🔵 |
| **Board** | Demandas de todos os times, com quem estão e o que está parado | Todos | 🔵 |
| **Kill switch** | Bloqueia ferramenta, modelo ou versão durante um incidente | Segurança, admin | 🟡 API, 🔵 tela |
| **Pessoas e acessos** | Perfis e squads, até o SSO assumir | Admin | 🔵 |
| **Meu painel** | O que é meu | Cada pessoa | 🟡 |

### 26.2 Gestão de features

**Interpretação:** "features" aqui são as funcionalidades do próprio Nexo, ou seja, módulos e flags do §6. As features de produto (`F-n`) ficam no Board.

```text
Features › demands.external_sync                                 beta
Sincronização com a ferramenta de gestão

Valor efetivo por escopo
  Global            desligada   padrão do manifesto
  time-posicao      ligada      portal, por gh:ana, 03/11: "piloto do GitHub Issues"
  time-traducao     desligada   cérebro 2026.11.02

Comandos afetados   nexo board --sync
Depende de          demands
Motivo (obrigatório) [                                        ]
[ Ligar para um time ]  [ Ligar para todos ]  [ Voltar ao padrão ]
```

| Regra | Comportamento |
|---|---|
| **Catálogo vem dos manifestos** | A tela lista o que os módulos declaram (§4.3), sem cadastro manual. Módulo novo aparece sozinho na próxima versão do central |
| **Piso visível e travado** | Política, auditoria, portões duros e demais itens do piso aparecem como "sempre ligado", sem botão. **O servidor recusa a alteração mesmo vinda direto pela API** |
| **Escopo** | Global, time ou projeto. A tela mostra a fonte vencedora de cada um (§6.1), inclusive quando o valor do cérebro está sendo sobreposto |
| **Motivo obrigatório** | Toda mudança guarda quem, quando, de quanto para quanto e por quê, e **pode ser desfeita** pelo histórico |
| **Efeito antes de confirmar** | Mostra módulos dependentes que também desligam e quantas máquinas estão naquele escopo, conforme a Frota |
| **Quando vale** | Até 2 minutos para quem está online. Offline, vale o último valor conhecido, e a tela não promete o contrário |
| **Ciclo de vida** | Mostra `experimental`, `beta`, `ga` e `deprecated`, com alerta de `remove_after` vencido |
| **Mesma regra na CLI** | `nexo flags set --global` chama a mesma API, com a mesma permissão e a mesma trilha |
| **Cérebro e portal** | O cérebro guarda o padrão versionado, e o portal, a operação do dia a dia. O alerta "diferente do cérebro há 30 dias" lembra de consolidar por PR |

### 26.3 Login: agora e com SSO

O acesso fica atrás de uma porta, `AuthProvider`, e **o restante do portal só conhece a pessoa e o perfil**. Conectar o SSO é configuração, não redesenho.

| | Fase 1: antes do SSO | Fase 2: SSO da empresa |
|---|---|---|
| **Como entra** | Convite com token por pessoa, guardado com hash e com validade | OIDC com PKCE, já existente na base (Entra ID ou outro) |
| **Perfil e squad** | Tela "Pessoas e acessos" | Grupos do provedor mapeados para perfil e squad 🔵, se ele enviar grupos |
| **Rede** | Só interna | Só interna |
| **Primeiro admin** | Criado por comando na implantação, com token de uso único | Mantido como acesso de emergência, com uso auditado |
| **Migração** | — | Liga a identidade atual à do SSO pelo e-mail ou usuário, sem perder histórico, e **desliga o login por token** |

**O login de token da base é descrito como "desenvolvimento/demo".** Por isso, a Fase 1 reforça com validade, revogação, limite de tentativas e auditoria. Ela existe só até a conexão do SSO (R-26).

**A identidade do portal é a mesma da telemetria:** a pseudonimização e a matriz (§15.3) valem igual nas duas fases.

### 26.4 O que muda em relação ao painel da base

- Catálogo de flags dinâmico, em vez da tupla fixa em `domain/config/flags.py`.
- Telas novas: Features, Board, Kill switch, Pessoas e configuração de insights.
- Perfis ganham o `pm` e o `qa`, que hoje não existem na matriz.
- Perfil `ci` da matriz sai: não há integração por Actions (D-27).
- Marca e textos passam para Nexo, sem compatibilidade com os nomes antigos.

## 27. Desvios da implementação

O que foi construído de forma diferente do texto acima, e por quê. O estado de cada prova está em [docs/estado.md](docs/estado.md).

| Tema | O texto diz | Como ficou | Motivo |
|---|---|---|---|
| **Comandos internos** | CLI montada só dos manifestos | Os comandos de papel vêm dos manifestos e apontam, por `target`, para comandos internos em `src/nexo/cli/commands`, ocultos na ajuda | Mantém uma só implementação de cada operação e as regras que já estavam testadas |
| **Recursos** | `resources` por módulo | Centralizados em `src/nexo/resources` | Política, receitas e skills são lidos por vários módulos, e a cascata com o cérebro já trabalha assim |
| **Módulo fala com módulo** | Por porta ou evento | Por `kernel.services`, serviço nomeado no manifesto, ou evento. O acoplamento lógico continua existindo, mas é declarado e não é mais de import | O import direto zerou; separar de fato cada par é trabalho contínuo |
| **Histórias em rascunho** | Aparecem no refinamento | Aparecem quando a prontidão chega a 100% | Evita história baseada em dúvida ainda aberta |
| **Perfil `ci` da matriz** | Sai | Virou `integration`, nunca atribuído a pessoa | O cartão de aprovação e a ingestão de integrações ainda precisam de um token que não é pessoa. Não tem relação com Actions |
| **Flags de demandas** | `demands.external_sync` | Mantida como `tracker_sync`, ao lado de `demands.board` e `demands.flow` | Nome já usado pelo código e pela política |
| **Adaptadores de repositório** | GitLab, Bitbucket e Azure Repos seguem (§14.4) | Seguem, mas sem prova e fora do MVP | Conforme o texto, registrado para não parecer esquecimento |
| **Worktree** | `%LOCALAPPDATA%\\nexo\\wt\\<hash>` | Vale só no Windows. Em macOS e Linux, continua em `.nexo/worktrees/` | Não há limite de caminho nesses sistemas |
| **Assinatura** | KMS é a pergunta Q-22 | `awskms://`, pela CLI da AWS da própria pessoa, sem dependência nova. O Azure Key Vault continua aceito | Segue o padrão assumido em Q-22 |
| **Descoberta no Kafka** | Tópicos, grupos e ACLs | Lê um JSON `{"grupo": ["tópico"]}` exportado pelo time de Kafka. Sem ACL nem schema registry | A credencial do cluster não entra no Nexo, e não há dependência nova. Falta conferir com Q-12 |
| **Efeito antes de confirmar flag** | Máquinas naquele escopo | Mostra o total de máquinas ativas na empresa e as dependências da flag | A tabela de dispositivos não guarda o squad. Por escopo, fica para depois |
| **Portão de contrato** | Contrato incompatível vai para decisão humana | Segura só o merge automático, com a flag `lineage.gate`, desligada até medir o scanner. Sem a flag, só avisa no PR | O §19 pede a medição antes de usar a linhagem no portão. AsyncAPI é o único formato comparado |
| **Produto e projeto** | "Produto" é o conjunto de repositórios | Na CLI, nos textos e na configuração, virou **projeto**, e cada pasta de código é um **repositório**: `nexo context project`, `context.project`. A pasta do cérebro é `projetos/`. No código, a classe segue `Product`, porque `project` já significa o repositório em todo o resto | Pedido do responsável. Trocar os identificadores internos criaria conflito sem ganho para quem usa |
| **Marca** | `brand.yaml` | O padrão usa a paleta aproximada do Itaú Unibanco, sem logo nem fonte da empresa. O código continua genérico: a marca fica só no `brand.yaml` e em `docs/` | Pedido do responsável. Não há manual público de marca: falta confirmar os valores com o time responsável |
| **Módulos do pós-MVP** | Entram depois do MVP | `deps`, `legacy`, `program`, `change`, `docsite`, `notify` e a travessia do `brain.graph` foram antecipados, com flag em `beta`, ligada | Pedido do responsável. O SSO fica para depois |
| **Validação local** | Inclui gitleaks, pip-audit, semgrep, SBOM e cobertura de domínio | gitleaks, pip-audit e semgrep rodam se instalados, e ficam como `não verificado` se não. SBOM e cobertura de domínio não estão no script | Falta decidir como instalá-los sem dependência travada no ambiente do projeto |

---

## Anexo — Fontes externas

Os itens sobre mercado vêm em boa parte de fontes secundárias e devem ser reconferidos na M4.

- [Agent Skills — especificação](https://agentskills.io)
- [obra/superpowers](https://github.com/obra/superpowers) — fluxo, clientes suportados e licença MIT (lidos diretamente)
- [soumatheusgomes/vibe-coding-toolkit](https://github.com/soumatheusgomes/vibe-coding-toolkit) — licença MIT (lido diretamente)
- [Comparativo Spec Kit, BMAD, OpenSpec e Superpowers](https://developertoolkit.ai/en/ecosystem/frameworks/spec-driven-frameworks/)
- [Spec Kit vs BMAD vs OpenSpec em 2026](https://dev.to/willtorber/spec-kit-vs-bmad-vs-openspec-choosing-an-sdd-framework-in-2026-d3j)
- [MCP Registry](https://modelcontextprotocol.info/tools/registry/) e [registries e gateways para allowlist](https://developertoolkit.ai/en/ecosystem/mcp/registry-and-gateways/)
- [Allowlists de MCP no Copilot](https://startdebugging.net/2026/08/copilot-mcp-allowlists-enterprise-managed-settings/)
- [Adoção do SKILL.md](https://www.noqta.tn/en/news/skill-md-open-standard-30-ai-coding-tools-adoption-2026), inclusive o relato de skills maliciosas
- [Padrão LLM Wiki com Obsidian](https://angelo-lima.fr/en/karpathy-second-brain-obsidian-claude-en/)
