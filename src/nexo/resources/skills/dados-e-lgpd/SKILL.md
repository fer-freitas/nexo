---
name: dados-e-lgpd
description: Classificação e tratamento de dados em negócio regulado — dado pessoal e sensível (LGPD), sigilo bancário, mascaramento, retenção, logs e ambientes de teste. Use ao definir requisitos, desenhar solução, modelar dados, escrever logs ou montar massa de teste.
---
# Dados e LGPD

## Classificação (defina para cada dado novo)
| Classe | Exemplos | Pode ir para log? | Pode ir para IA externa? | Ambiente de teste |
|---|---|---|---|---|
| Público | nome de produto, tabela de tarifas | sim | sim | real |
| Interno | ids técnicos, métricas agregadas | sim | sim | real |
| Confidencial | dados de conta, transações, limites | não (só id) | só com redação | sintético |
| Pessoal (LGPD) | nome, CPF, e-mail, telefone, endereço, IP | não | não | sintético ou anonimizado |
| Pessoal sensível | saúde, biometria, origem racial, religião | não | não | sintético |
| Sigilo bancário | saldo, extrato, movimentação por cliente | não | não | sintético |

> **A definir pela organização:** tabela oficial de classificação e o responsável (DPO) por dúvidas.

## Regras
- **Minimização:** colete só o que a funcionalidade precisa. Todo campo pessoal novo tem finalidade escrita no requisito.
- **Base legal:** o PM registra a base legal (contrato, obrigação legal, legítimo interesse, consentimento) de cada tratamento novo.
- **Logs:** nunca registre CPF, nome, e-mail, telefone, número de cartão, token ou senha. Use o id interno ou o dado mascarado (`***.456.789-**`).
- **Eventos e mensagens:** payload de SQS, SNS e Kafka leva ids, não dados pessoais, salvo quando o consumidor precisa e está autorizado. Criptografia em trânsito e em repouso sempre ligada.
- **Retenção:** todo dado tem prazo de guarda e rotina de descarte; registre no desenho técnico.
- **Direitos do titular:** o desenho diz como localizar, exportar e apagar os dados de uma pessoa.
- **Testes:** massa de dados sintética. Cópia de produção só anonimizada e com aprovação.
- **IA:** o Nexo redige segredos e dados pessoais antes de enviar a qualquer ferramenta; mesmo assim, não cole dados reais em prompts, histórias ou comentários.

## Checklist por etapa
- **PM:** dados pessoais listados, finalidade e base legal registradas, prazo de guarda definido.
- **Tech lead:** classe de cada dado no desenho, onde é criptografado, quem acessa, como é apagado, o que vai para log e evento.
- **Dev:** nenhum dado pessoal em log, exceção, métrica ou nome de arquivo; mascaramento em respostas de API quando exigido.
- **QA:** massa sintética; teste de que o log não contém dado pessoal nos fluxos principais.
- **Revisão:** qualquer campo pessoal novo sem finalidade escrita bloqueia a aprovação.
