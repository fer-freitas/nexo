---
name: dependencias-seguras
description: Gestão segura de dependências NuGet/npm/pip — fontes aprovadas, versões fixadas, lock files, licenças permitidas, atualização de vulnerabilidades pelo Nexo e pacotes novos. Use ao adicionar, atualizar ou avaliar um pacote.
---
# Dependências seguras

## Adicionar pacote novo
- Só de fontes aprovadas (feed interno/proxy da empresa).
- Verifique: mantido (último release < 12 meses), licença permitida, popularidade, vulnerabilidades conhecidas, dependências transitivas.
- Pacote novo é decisão do tech lead: agentes do Nexo não adicionam dependência (registram bloqueio e param).

> **A definir pela organização:** lista de licenças permitidas e proibidas; feed oficial.

## Versões
- Versões fixadas; `Directory.Packages.props` (Central Package Management) no .NET; lock files commitados (`packages.lock.json`, `package-lock.json`, `requirements*.txt` com hashes do `pip-compile`).
- Restore com lock na esteira (`--locked-mode`).

## Atualização
- Vulnerabilidades e desatualizações viram histórias automaticamente (`nexo deps scan` / `nexo deps plan`); o Nexo aplica a atualização com o gerenciador da stack e a esteira testa; a IA só entra se quebrar.
- Atualização major exige leitura das notas de versão e teste de integração.
- Nunca desabilitar o aviso de vulnerabilidade para "passar a esteira".

## Checklist
- [ ] Fonte aprovada, licença permitida
- [ ] Versão fixada e lock atualizado
- [ ] SCA sem high/critical
