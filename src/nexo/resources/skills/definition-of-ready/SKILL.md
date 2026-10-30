---
name: definition-of-ready
description: Definition of Ready do fluxo — o modelo único de refinamento de cada história (critérios de aceite, técnicos, de teste e de performance, observabilidade, deploy e mudança) e o portão que o Nexo confere antes do desenvolvimento. Use ao refinar, ao revisar se uma história pode começar e ao preencher o refinamento.yaml.
---
# Definition of Ready (DoR)

Uma história só entra em desenvolvimento quando o refinamento segue **o mesmo modelo** para todos os squads e o portão de DoR do Nexo passa (`nexo ready <história>`). O portão é determinístico: confere presença, formato e números; não julga mérito (isso é do tech lead).

## Modelo único de refinamento (`refinamento.yaml` por história)
Arquivo em `.nexo/specs/<feature>/refinamento/<US-id>.yaml`:

```yaml
versao_modelo: 1
historia: US-3-1
criterios_de_aceite: [AC-1, AC-2, AC-3]      # todos em Gherkin na história
criterios_tecnicos:
  - id: CT-1
    descricao: Consumidor SQS idempotente pela chave idempotency-key
    verificacao: teste                       # teste | revisao | metrica
testes:
  unitario:
    cobertura_minima_linhas_alteradas: 80
  integracao:
    - id: CI-1
      cenario: Mensagem na fila gera registro e publica EstornoSolicitado no SNS
  contrato:
    aplica: true
    artefato: contracts/estorno-solicitado.asyncapi.yaml
  performance:
    aplica: true
    cenario: POST /estornos com 200 req/s por 10 minutos
    carga: {tipo: rps, valor: 200, duracao: 10m}
    limites: {p95_ms: 300, p99_ms: 800, erro_max_pct: 0.5}
    justificativa_nao_aplica: ""
  mutacao:
    score_minimo: 70
  seguranca:
    bloqueia_em: high                        # high | critical
    itens: [Validação de entrada no valor do estorno, IAM só sqs:ReceiveMessage na fila]
observabilidade:
  logs: [estorno.solicitado com id da compra, sem dados pessoais]
  metricas: [estornos_solicitados_total, estorno_latencia_ms]
  alarmes: [erro > 1% em 5 min, idade da mensagem na fila > 2 min]
dados:
  - {campo: valor, classe: confidencial}
deploy:
  estrategia: canario                        # canario | blue_green | rolling | feature_flag
  rollback: Voltar o alias da Lambda para a versão anterior
  feature_flag: estorno-app
mudanca:
  risco: baixo                               # baixo | medio | alto
  candidata_mudanca_padrao: true
dependencias: [Contrato do tópico com o time de Conciliação]
riscos: [Pico do dia 5 útil]
estimativa: {pontos: 3}
```

## O que o portão confere
| Item | Regra |
|---|---|
| Critérios de aceite | Todos os ids existem na história, em Gherkin |
| Critérios técnicos | Pelo menos um; cada um com forma de verificação |
| Unitário | Cobertura mínima definida (≥ piso da org) |
| Integração | Pelo menos um cenário quando a história toca fila, tópico, banco ou API externa |
| Contrato | Obrigatório quando cria ou muda API ou evento |
| **Performance** | `aplica: true` com carga, duração e limites numéricos (p95, p99, erro); ou `aplica: false` com justificativa aceita pelo tech lead. Endpoint novo, consumidor novo ou mudança em caminho crítico: performance é obrigatória |
| Mutação | Score mínimo definido (≥ piso da org) |
| Segurança | Nível de bloqueio definido |
| Observabilidade | Logs, métricas e ao menos um alarme |
| Dados | Todo campo novo classificado (`dados-e-lgpd`) |
| Deploy | Estratégia e rollback escritos |
| Mudança | Risco classificado |
| Estimativa | Pontos em 1, 2, 3, 5 ou 8 |
| Dependências | Externas com dono e data |

## Performance é um teste do fluxo
Os limites do refinamento viram os **thresholds do teste de carga** na esteira. Se o p95, o p99 ou a taxa de erro passarem do limite, a história volta para correção como qualquer teste falhando. Ver `testes-de-carga`.

## Regras
- Mesmo modelo para todos: não crie seções próprias; o que não couber vai em `riscos` ou `dependencias`.
- Números sempre com unidade; nada de "rápido" ou "alto volume".
- `aplica: false` exige justificativa; o portão rejeita justificativa vazia.
- Mudou o escopo depois do DoR? Refinamento de novo e portão de novo.

## Checklist rápido do tech lead
- [ ] refinamento.yaml completo e no modelo
- [ ] Performance com números vindos de `requisitos-nao-funcionais-negocio`
- [ ] Critérios de teste cobrem integração, contrato, carga, mutação e segurança
- [ ] `nexo ready` aprovado
