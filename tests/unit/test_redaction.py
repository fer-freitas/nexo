from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nexo.kernel.security.redaction import contains_sensitive, redact, redact_obj, valid_cnpj, valid_cpf, valid_luhn


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD fim", "github_token"),
        ("AKIAABCDEFGHIJKLMNOP", "aws_access_key"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123", "bearer"),
        ("Server=db;User=sa;Password=SuperSecreta123;", "connection_secret"),
        ("a senha: MinhaSenha#2026", "connection_secret"),
        ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "anthropic_key"),
        ("cliente CPF 529.982.247-25", "cpf"),
        ("cnpj 11.222.333/0001-81", "cnpj"),
        ("cartão 4111 1111 1111 1111", "credit_card"),
        ("fale com joao.silva@empresa.com.br", "email"),
        ("ligue (11) 98765-4321", "phone_br"),
        ("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "private_key"),
    ],
)
def test_redacts_sensitive_values(text: str, kind: str) -> None:
    out = redact(text)
    assert f"[REDACTED:{kind}]" in out


@pytest.mark.parametrize(
    "text",
    [
        "cpf inválido 123.456.789-00",
        "número 4111 1111 1111 1112",
        "commit 9f1c2ab e versão 1.2.3",
        "hash " + "a1" * 32,
        "story 124 com 5 pontos",
    ],
)
def test_keeps_non_sensitive_values(text: str) -> None:
    assert redact(text) == text
    assert not contains_sensitive(text)


def test_validators() -> None:
    assert valid_cpf("52998224725")
    assert not valid_cpf("11111111111")
    assert valid_cnpj("11222333000181")
    assert not valid_cnpj("11222333000180")
    assert valid_luhn("4111111111111111")
    assert not valid_luhn("123")


def test_redact_obj_recurses() -> None:
    data = {"a": ["x", "Password=abcdef123"], "b": {"c": "ok"}, "n": 3}
    out = redact_obj(data)
    assert out["a"][1] == "Password=[REDACTED:connection_secret]"
    assert out["b"] == {"c": "ok"}
    assert out["n"] == 3


@given(st.text(alphabet=st.characters(blacklist_categories=("Nd",)), max_size=200))
def test_text_without_digits_or_markers_is_stable(text: str) -> None:
    # Redação é idempotente.
    once = redact(text)
    assert redact(once) == once


def test_accountable_identity_stays_readable_in_audit() -> None:
    from nexo.kernel.security.redaction import redact_obj

    out = redact_obj({"approver": "sso:caio@empresa.com", "reason": "ligar para caio@empresa.com", "by": "caio@x.com"})
    assert out["approver"] == "sso:caio@empresa.com"  # identidade de quem decidiu: finalidade da trilha
    assert "caio@empresa.com" not in out["reason"] and "caio@x.com" not in out["by"]  # fora do formato: redigido


def test_example_emails_survive_redaction_and_real_ones_do_not() -> None:
    from nexo.kernel.security.redaction import redact

    text = "Exemplo: joao.silva@exemplo.com vira j***@exemplo.com; cliente real: maria@banco.com.br; teste a@b.test"
    out = redact(text)
    assert "joao.silva@exemplo.com" in out and "j***@exemplo.com" in out and "a@b.test" in out
    assert "maria@banco.com.br" not in out and "[REDACTED:email]" in out
