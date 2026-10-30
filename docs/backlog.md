# Backlog

O que ainda precisa ser desenvolvido. Para incluir um item, acrescente uma linha em **Novos**. Quando ele entrar em desenvolvimento, passe para **Próximos**, e apague a linha quando estiver feito: o histórico fica no Git.

O que depende de prova real, e não de código, está em [provas-reais.md](provas-reais.md). O que já foi provado está em [estado.md](estado.md).

## Novos

| Item | Por que importa |
|---|---|
| | |

## Próximos

| Item | Por que importa | Origem |
|---|---|---|
| SBOM e cobertura de domínio em `scripts/validate.py` | A spec pede, e hoje não rodam | §20, §27 |
| Instalar `pip-audit` e `semgrep` sem travar dependências do projeto | Hoje ficam como "não verificado" | §27 |
| Efeito da flag por escopo no portal | A tela só mostra o total de máquinas da empresa, porque a tabela de dispositivos não guarda o squad | §26.2, §27 |
| ACL e schema registry na descoberta do Kafka | Hoje só lê grupos e tópicos de um JSON exportado | §23.5, §27 |
| Tirar as dependências do central do binário da CLI | O binário tem 319 MB e leva `pyarrow`, que só o central usa | Build em macOS |

## Depois do MVP

Conexão com o SSO da empresa, `eval` e harness, `brain.graph`, `deps`, `legacy`, `program`, `notify`, `autonomy`, `isolation`, `docsite`, insights e skills do Nexo expostas dentro das próprias CLIs de IA. Detalhes no §19 da [spec](../SPEC.md).
