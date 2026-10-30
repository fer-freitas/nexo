---
name: testes-de-carga
description: Testes de carga e performance como etapa do fluxo — cenários com k6 a partir dos limites do refinamento (p95, p99, taxa de erro, vazão), APIs e consumidores de fila, ambiente, dados e leitura dos resultados. Use quando o refinamento.yaml tiver performance aplicável ou ao investigar lentidão.
---
# Testes de carga e performance

Performance é **uma etapa de teste do fluxo**: os limites definidos no refinamento (`definition-of-ready`) viram os thresholds do teste, e o teste reprova a história se passar do limite, como qualquer outro teste.

## Do refinamento para o script
```yaml
# refinamento.yaml
testes:
  performance:
    aplica: true
    cenario: POST /estornos com 200 req/s por 10 minutos
    carga: {tipo: rps, valor: 200, duracao: 10m}
    limites: {p95_ms: 300, p99_ms: 800, erro_max_pct: 0.5}
```
```javascript
// tests/load/estornos.js  (k6)
import http from 'k6/http';
import { check } from 'k6';
export const options = {
  scenarios: {
    estornos: { executor: 'constant-arrival-rate', rate: 200, timeUnit: '1s', duration: '10m',
                preAllocatedVUs: 100, maxVUs: 400 },
  },
  thresholds: {
    http_req_duration: ['p(95)<300', 'p(99)<800'],
    http_req_failed: ['rate<0.005'],
  },
};
export default function () {
  const r = http.post(`${__ENV.BASE_URL}/v1/estornos`, JSON.stringify(payload()),
    { headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() } });
  check(r, { 'status 202': (res) => res.status === 202 });
}
```
- `constant-arrival-rate` para vazão (rps); rampa (`ramping-arrival-rate`) quando o objetivo é achar o limite.
- Os thresholds são **exatamente** os limites do refinamento; o Nexo confere que batem **[portão]**.

## Consumidores de fila e tópico
- Carga = publicar N mensagens/s na fila/tópico por um período (script k6 com SDK, ou gerador próprio).
- Medir: idade da mensagem mais antiga, vazão de processamento, lag (Kafka), erros e DLQ.
- Limites típicos: "idade máxima < 60 s com 500 msg/s por 15 min; DLQ = 0".

## Tipos de teste
| Tipo | Objetivo | Quando |
|---|---|---|
| Carga (pico) | Confirma os limites no pico previsto | Sempre que `aplica: true` |
| Estresse | Descobre onde quebra e como se recupera | Componente novo ou mudança de arquitetura |
| Resistência (soak) | Vazamento de memória/conexões em horas | Serviço de longa duração (ECS, Kafka) |
| Cold start | Latência da primeira invocação | Lambda síncrona com meta de latência |

## Ambiente e dados
- Ambiente de performance com configuração equivalente à produção (mesmos limites de concorrência, memória, tipo de banco); nunca em produção.
- Dados sintéticos em volume realista; dependências externas simuladas com a latência real informada.
- Aquecer antes de medir (exceto teste de cold start).

## Resultado
- Relatório anexado à história e ao pacote de evidências (requisição de mudança e mudança padrão).
- Reprovou: analisar gargalo (CPU, memória, banco, dependência, concorrência), corrigir e rodar de novo; **não afrouxe o limite** sem nova aprovação do tech lead e do PM.

## Checklist
- [ ] Script em `tests/load/` com thresholds iguais aos limites do refinamento
- [ ] Carga do pico previsto pelo negócio
- [ ] Ambiente equivalente e dados sintéticos
- [ ] Relatório anexado
