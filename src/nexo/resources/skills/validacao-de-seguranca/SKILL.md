---
name: validacao-de-seguranca
description: Como ler, corrigir e justificar achados da validação de segurança — SAST (Semgrep/Sonar), SCA (vulnerabilidades de pacotes), segredos, varredura de IaC e de imagem — e o que bloqueia a entrega. Use quando a etapa de segurança da esteira apontar achados ou ao revisar segurança.
---
# Validação de segurança

O Nexo roda as ferramentas de forma determinística na esteira e um revisor de segurança de outro fornecedor revisa o diff. Esta skill ensina a tratar o resultado.

## Ferramentas e o que bloqueia
| Etapa | O que procura | Bloqueia |
|---|---|---|
| SAST | Injeção, XSS, criptografia fraca, desserialização, IDOR óbvio | severidade ≥ `bloqueia_em` do refinamento **[portão]** |
| SCA | Pacotes com CVE | high/critical sem correção aceita **[portão]** |
| Segredos (gitleaks) | Chaves, tokens, senhas no diff | qualquer achado **[portão]** |
| IaC (tfsec/checkov) | IAM com `*`, recurso público, sem criptografia | high/critical **[portão]** |
| Imagem | CVEs da imagem base | high/critical |
| Revisão de segurança (IA de outro fornecedor) | Lógica: autorização, fluxo de dinheiro, dados pessoais | achado bloqueante → volta como bug |

> **A definir pela organização:** ferramentas oficiais, severidade mínima bloqueante e processo de exceção (quem aprova e por quanto tempo).

## Tratando cada achado
1. **Entenda** o fluxo de dados: a entrada chega até ali controlada por quem?
2. **Corrija na causa** (consulta parametrizada, validação, autorização, criptografia padrão), não silenciando a regra.
3. **Teste** que reproduz o problema e passa depois da correção.
4. **Falso positivo:** supressão no ponto exato (`// nosemgrep: <regra> — motivo`), com justificativa verificável; nunca desligar a regra no projeto.
5. **Pacote vulnerável:** atualizar para a versão corrigida (o Nexo faz isso sem IA: `dependencias-seguras`); sem correção, exceção formal com prazo.
6. **Segredo vazado:** revogar e rotacionar **primeiro**, depois remover do código; o histórico do git continua com o segredo.

## Checklist
- [ ] Nenhum achado bloqueante aberto
- [ ] Correções com teste
- [ ] Supressões justificadas no ponto
- [ ] Segredo vazado revogado e rotacionado
