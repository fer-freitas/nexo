---
name: modelagem-de-ameacas
description: Modelagem de ameaças leve (STRIDE) por fluxo, no desenho técnico — ameaças, controles e o que vira critério técnico ou de teste de segurança. Use em features que expõem API, recebem dados de fora, movimentam dinheiro ou tratam dados pessoais.
---
# Modelagem de ameaças

Feita pelo tech lead no SDD, em 15–30 minutos por fluxo. O resultado vira controles no desenho e critérios técnicos (`criterios-tecnicos-de-aceite`).

## Passos
1. Desenhe o fluxo com as fronteiras de confiança (internet, rede interna, conta AWS, terceiros).
2. Para cada elemento que cruza fronteira, passe o STRIDE.
3. Para cada ameaça relevante: controle, onde fica e como é verificado.

## STRIDE com exemplos do nosso contexto
| Ameaça | Pergunta | Controles típicos |
|---|---|---|
| **S**poofing | Alguém pode se passar por cliente ou serviço? | OAuth2/JWT validado, mTLS entre serviços, IAM por função |
| **T**ampering | Alguém pode alterar valor, mensagem ou arquivo? | Validação de entrada, assinatura/HMAC, criptografia, permissão de escrita mínima |
| **R**epudiation | Alguém pode negar que fez? | Log de auditoria com quem, quando e o quê; correlation id |
| **I**nformation disclosure | Dados podem vazar? | Mascaramento, criptografia, sem dado pessoal em log/evento, IAM mínimo |
| **D**enial of service | Dá para derrubar ou encarecer? | Rate limit, WAF, limite de concorrência, timeouts, tamanho máximo de payload |
| **E**levation of privilege | Alguém pode ganhar acesso indevido? | Autorização por recurso (o cliente só vê o próprio estorno), IAM sem `*` |

## Pontos de atenção em finanças
- IDOR: sempre verificar se o recurso pertence a quem pede.
- Valores: validar limites, sinal e casas decimais no servidor.
- Replay: idempotência e expiração de tokens e links.
- Mensagens: consumidor valida o esquema e a origem.

## Saída no SDD
| Id | Fluxo | Ameaça | Controle | Verificação (CT/teste) |
|---|---|---|---|---|
| AM-1 | POST /estornos | IDOR no id da compra | Verificar dono da compra | CT-9 teste de integração |

## Checklist
- [ ] Fronteiras de confiança desenhadas
- [ ] STRIDE aplicado nos elementos que cruzam fronteira
- [ ] Cada ameaça relevante com controle e verificação
