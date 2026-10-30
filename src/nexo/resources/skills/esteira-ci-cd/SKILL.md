---
name: esteira-ci-cd
description: A esteira de CI/CD do fluxo — etapas de build, qualidade, testes (unitário, integração, contrato, carga, mutação), segurança, artefato único, promoção dev → homologação → produção e aprovações. Use ao configurar a esteira de um serviço ou entender por que uma etapa reprovou.
---
# Esteira de CI/CD

O Nexo não cria nem altera a esteira. Ele lê os comandos do pipeline existente (`nexo tl setup`) e roda as mesmas etapas localmente antes do PR.

> **A definir pela organização:** plataforma de CI/CD oficial, contas/ambientes e aprovadores por ambiente.

## Etapas (na ordem) e portões
| # | Etapa | Reprova quando |
|---|---|---|
| 1 | Restore com lock, build com avisos como erro | erro de build, aviso |
| 2 | Formatação e analisadores | diferença de formatação, regra violada |
| 3 | Testes unitários + cobertura das linhas alteradas | teste falhando, cobertura < mínimo do refinamento |
| 4 | Testes de arquitetura | dependência proibida |
| 5 | Testes de aceite (rastreio AC-n/CT-n) | critério sem teste ou falhando |
| 6 | Testes de integração e contrato | falha |
| 7 | Mutação nos arquivos alterados | score < mínimo do refinamento |
| 8 | Segurança: SAST, SCA, segredos, IaC, imagem | achado ≥ nível de bloqueio |
| 9 | Build do artefato único (imagem/pacote) com tag = sha | — |
| 10 | Deploy em **dev** + smoke test | smoke falhando |
| 11 | Deploy em **homologação** + **teste de carga/performance** com os limites do refinamento | p95/p99/erro acima do limite |
| 12 | Pacote de evidências (Nexo) + requisição de mudança | evidência faltando |
| 13 | Deploy em **produção** (estratégia do refinamento) + validação pós-deploy | alarme/SLO violado → rollback |

## Regras
- **Artefato único:** o mesmo artefato promovido entre ambientes; nunca rebuild por ambiente. Configuração por ambiente fora do artefato.
- Etapas 1–8 no PR; 9–13 após o merge na branch principal.
- Aprovação humana antes de homologação (quando exigido) e sempre antes de produção.
- Esteira vermelha não se contorna: corrige-se a causa. Etapa desligada exige exceção formal.
- Tempo da esteira até o PR ≤ 15 min: paralelize etapas e rode mutação só no que mudou.

## Checklist
- [ ] Todas as etapas presentes e na ordem
- [ ] Limites da cobertura, mutação e carga vindos do refinamento
- [ ] Artefato único promovido
- [ ] Aprovação humana para produção
