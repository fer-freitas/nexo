---
name: tracker-de-trabalho
description: Como criar, ler e atualizar itens de trabalho no GitHub Issues pela integração do Nexo ou por MCP — hierarquia épico, feature, história e tarefa, campos obrigatórios, estados, tags e vínculos. Use sempre que for registrar ou consultar trabalho no tracker.
---
# Tracker de trabalho

O tracker é a fonte oficial do trabalho do time. O Nexo sincroniza com ele pela integração nativa (`nexo tracker sync`) ou por um servidor MCP do próprio tracker. Esta skill diz **como** preencher; a integração faz a chamada.

## Equivalência de termos com o GitHub Issues
| Nexo | GitHub Issues |
|---|---|
| Épico | Milestone ou issue com label `epic` |
| Feature | Issue com label `feature` |
| História | Issue |
| Tarefa | Task list ou issue filha |
| Bug | Issue com label `bug` |

O de-para de estados e tipos de cada projeto fica em `.nexo/mappings` (`nexo tracker map --check`).

## Hierarquia
```
Épico (objetivo de negócio, trimestre)
└── Feature (entrega de valor, algumas sprints)
    └── História (valor para o usuário, cabe na sprint)
        └── Tarefa (unidade técnica, um dia ou menos)
```
- Toda história pertence a uma feature; toda feature, a um épico.
- Bug aponta para a história ou a feature onde foi encontrado.

## Campos obrigatórios por tipo
| Campo | Épico | Feature | História | Tarefa |
|---|---|---|---|---|
| Título (verbo + resultado) | ✔ | ✔ | ✔ | ✔ |
| Descrição / narrativa | objetivo e métrica | problema, escopo, fora de escopo | "Como… quero… para…" | o que fazer e onde |
| Critérios de aceite | — | resumo | Gherkin completo | critérios técnicos |
| Critérios técnicos e de teste | — | — | ✔ (após refinamento técnico) | ✔ |
| Pontos / estimativa | — | — | ✔ | horas |
| Time e responsável | ✔ | ✔ | ✔ | ✔ |
| Tags / labels | produto | produto, release | stack, risco, `nexo` | tipo |
| Links | — | spec, discovery | SDD, ADRs | PR |

> **A definir pelo time:** campos personalizados obrigatórios do tracker e nomes exatos dos estados.

## Estados e quem move
`Novo → Em discovery → Pronto para refinamento → Em refinamento técnico → Pronto (DoR) → Em desenvolvimento → Em revisão → Em homologação → Pronto para produção → Concluído`
- **Pronto (DoR)** só com o portão de Definition of Ready aprovado no Nexo (`definition-of-ready`).
- **Concluído** só com o PR integrado, o deploy em produção e a mudança fechada (`gestao-de-mudanca`).

## Regras
- Crie a história no tracker **depois** que o PM aprovar o pacote no Nexo; o id do tracker volta para o `backlog.yaml` (vínculo externo) e para o nome da branch e do PR.
- Nunca duplique: busque pelo título e pela feature antes de criar.
- Atualize o estado pelo fluxo; não pule estados.
- Comentário no tracker nunca leva dado pessoal, segredo ou trecho de log com dado de cliente.
- Label `nexo` em tudo que for executado pelo Nexo (métrica de adoção).
- Sem API disponível: `nexo tracker export` / `import` trocam os itens por CSV.

## Checklist antes de gravar
- [ ] Item pai existe e está correto?
- [ ] Todos os campos obrigatórios do tipo preenchidos?
- [ ] Critérios de aceite em Gherkin (história)?
- [ ] Links para spec, SDD e ADRs?
- [ ] Time, responsável e labels definidos?
