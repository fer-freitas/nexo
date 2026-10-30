---
name: secure-coding
description: Codificação segura para negócio regulado em .NET e AWS — OWASP Top 10/ASVS, autorização por recurso, validação, injeção, segredos, criptografia, dependências, logs sem dados pessoais e LGPD. Use ao implementar ou revisar código.
---
# Codificação segura

## Entrada e saída
- Valide toda entrada na borda (tipo, tamanho, formato, faixa); rejeite por padrão. Limite tamanho de payload e de coleções.
- Valores monetários: sinal, casas decimais e limites validados no servidor.
- Saída codificada conforme o destino; erros sem stack trace para o cliente (`ProblemDetails`).

## Autenticação e autorização
- Autorização em todo endpoint (negar por padrão, política explícita).
- **Autorização por recurso (IDOR):** sempre conferir se o recurso pertence a quem pede; nunca confiar em id vindo do cliente.
- Tokens validados (emissor, audiência, expiração, assinatura); sem lógica própria de JWT.
- Entre serviços: identidade de serviço (IAM/mTLS), não chaves estáticas.

## Injeção
- Consultas parametrizadas (EF Core/Dapper com parâmetros); nada de concatenar SQL, comandos, expressões do DynamoDB ou filtros.
- Desserialização com tipos conhecidos (`System.Text.Json`); nunca `TypeNameHandling` permissivo.

## Segredos
- Só via Secrets Manager/Parameter Store (ou cofre da empresa); nunca no código, testes, `appsettings`, variáveis de ambiente de imagem ou logs.
- Rotação suportada (não cachear para sempre).

## Criptografia
- TLS 1.2+ em tudo; criptografia em repouso (KMS) ligada.
- Bibliotecas padrão da plataforma; nada de criptografia caseira; hash de senha com algoritmo adaptativo; aleatoriedade com `RandomNumberGenerator`.

## Dependências
- Pacotes de fontes aprovadas, versão fixada, sem vulnerabilidade high/critical (`dependencias-seguras`) **[portão SCA]**.

## Dados e logs
- Logs estruturados sem dados pessoais (mascarar CPF, e-mail, cartão); minimização, finalidade e retenção (LGPD) — ver `dados-e-lgpd`.

## AWS
- IAM mínimo por função/tarefa (`iam-e-rede-aws`); nada público por padrão (buckets, filas, APIs).

## Checklist
- [ ] Entrada validada e autorização por recurso
- [ ] Nenhuma consulta concatenada
- [ ] Nenhum segredo fora do cofre
- [ ] Nenhum dado pessoal em log
- [ ] SAST e SCA sem achados bloqueantes
