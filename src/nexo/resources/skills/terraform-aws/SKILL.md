---
name: terraform-aws
description: Infraestrutura como código com Terraform na AWS — módulos da empresa, estado remoto, ambientes, nomes e tags, segurança, plan revisado e esteira. Use ao criar ou alterar recursos AWS (Lambda, ECS, SQS, SNS, MSK, IAM, alarmes).
---
# Terraform na AWS

> **A definir pela organização:** módulos oficiais, backend de estado, contas por ambiente, tags obrigatórias e política de aprovação de infraestrutura.

## Estrutura
```
infra/
  modules/            # só se não houver módulo oficial da empresa
  envs/
    dev/  hml/  prd/  # mesma composição, variáveis diferentes
      main.tf  variables.tf  terraform.tfvars  backend.tf
```
- Use os **módulos oficiais da empresa** (fila com DLQ e alarmes, Lambda com role mínima, serviço ECS). Recurso cru só com justificativa.
- Versões fixadas: `required_version`, providers e módulos com versão exata.

## Estado
- Backend remoto com lock (S3 + DynamoDB ou o padrão da empresa), um estado por serviço e ambiente; nunca estado local ou no git.

## Nomes e tags
- Nome: `<produto>-<servico>-<recurso>-<ambiente>`.
- Tags obrigatórias em tudo (`default_tags` no provider): produto, squad, centro de custo, ambiente, classificação de dados, repositório.

## Segurança
- IAM mínimo: sem `*` em ações ou recursos de produção (`iam-e-rede-aws`).
- Criptografia ligada (KMS) em filas, tópicos, buckets, logs e bancos.
- Sem segredos em `.tf`/`.tfvars`: referência ao Secrets Manager.
- Varredura de IaC (tfsec/checkov ou padrão da empresa) na esteira **[portão]**.

## Fluxo
- `terraform fmt -check` e `validate` **[portão]**; `plan` gerado na esteira e anexado ao PR.
- **Nunca `apply` manual** nem pelo agente: o `apply` roda na esteira, com aprovação humana em homologação e produção.
- Mudança destrutiva (replace/destroy) no plan exige aprovação explícita do tech lead.
- Drift: detectar e corrigir pelo código, nunca pelo console.

## Checklist
- [ ] Módulos oficiais e versões fixadas
- [ ] Estado remoto por serviço e ambiente
- [ ] Tags obrigatórias
- [ ] IAM mínimo, criptografia, sem segredos
- [ ] Plan anexado ao PR, sem destroy inesperado
