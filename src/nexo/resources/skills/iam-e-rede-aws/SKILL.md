---
name: iam-e-rede-aws
description: IAM de menor privilégio e rede na AWS para Lambda, ECS, SQS, SNS, Kafka e bancos — políticas por recurso, roles separadas, condições, security groups, endpoints privados e criptografia com KMS. Use ao escrever políticas IAM, Terraform de rede ou revisar permissões.
---
# IAM e rede na AWS

## IAM
- Uma role por função Lambda e por task ECS (task role separada da execution role).
- Ações específicas e recursos específicos: nada de `*` em produção.
```json
{
  "Effect": "Allow",
  "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
  "Resource": "arn:aws:sqs:sa-east-1:123456789012:pagamentos-estornos-prd"
}
```
- Produtor SNS: só `sns:Publish` no tópico dele. Consumidor: só a própria fila.
- KMS: `kms:Decrypt`/`GenerateDataKey` só na chave usada pelos recursos do serviço.
- Secrets Manager: `GetSecretValue` só nos segredos do serviço.
- Condições quando fizer sentido (`aws:SourceArn` em políticas de fila inscrita em tópico, `aws:PrincipalOrgID`).
- Sem usuários IAM com chave de acesso para aplicações; sem credencial em código.
- Permissões de humanos em produção só por perfil e com trilha (CloudTrail).

## Políticas de recurso
- Fila SQS inscrita em SNS: permite `sqs:SendMessage` só do ARN do tópico.
- Buckets: bloqueio de acesso público ligado; política negando transporte sem TLS.

## Rede
- Serviços em subnets privadas; entrada só por load balancer/API Gateway.
- Security groups: origem = security group do chamador, não CIDR amplo; saída restrita quando possível.
- VPC endpoints (interface/gateway) para SQS, SNS, S3, Secrets Manager e STS, evitando tráfego pela internet.
- Kafka (MSK): autenticação IAM ou mTLS, criptografia em trânsito.

## Checklist
- [ ] Role por função/tarefa com ações e recursos específicos
- [ ] Políticas de recurso restritas ao produtor/consumidor
- [ ] Rede privada, SG por referência, endpoints privados
- [ ] Varredura de IaC sem achados high/critical
