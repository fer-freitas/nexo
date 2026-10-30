from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nexo import __version__
from nexo.kernel.context import AppContext
from nexo.kernel.errors import IntegrityFailure, NexoError
from nexo.modules.setup.app import selfmanage


def keypair() -> tuple[Ed25519PrivateKey, str]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, base64.b64encode(pub).decode()


def app_zip(marker: bytes, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(selfmanage.executable_name(), marker)
        zf.writestr("_internal/base_library.zip", b"lib")
        for name, content in (extra or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def publish(tmp: Path, version: str, payload: bytes, priv: Ed25519PrivateKey, sha: str | None = None) -> Path:
    (tmp / "nexo-bin").write_bytes(payload)
    manifest = {
        "version": version,
        "artifacts": {
            selfmanage.platform_key(): {
                "url": "nexo-bin",
                "sha256": sha or hashlib.sha256(payload).hexdigest(),
                "signature": base64.b64encode(priv.sign(payload)).decode(),
            }
        },
    }
    path = tmp / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


@pytest.fixture
def ctx(nexo_home: Path, tmp_path: Path) -> AppContext:
    return AppContext(cwd=tmp_path, home=nexo_home)


def test_update_replaces_install_dir_when_signed(ctx: AppContext, tmp_path: Path) -> None:
    priv, pub = keypair()
    manifest = publish(tmp_path, "99.0.0", app_zip(b"new-binary"), priv)
    target = tmp_path / "app"
    (target / "_internal").mkdir(parents=True)
    (target / selfmanage.executable_name()).write_bytes(b"old")
    result = selfmanage.update(ctx, str(manifest), trusted_keys=(pub,), target=target)
    assert result == {"updated": True, "version": "99.0.0"}
    assert (target / selfmanage.executable_name()).read_bytes() == b"new-binary"
    assert not target.with_name("app.old").exists() and not target.with_name("app.new").exists()
    assert [e.type for e in ctx.events.iter_all()][-1] == "nexo.update"


def test_update_rejects_zip_slip(ctx: AppContext, tmp_path: Path) -> None:
    priv, pub = keypair()
    manifest = publish(tmp_path, "99.0.0", app_zip(b"x", {"../../evil": b"x"}), priv)
    with pytest.raises(IntegrityFailure, match="inseguro"):
        selfmanage.update(ctx, str(manifest), trusted_keys=(pub,), target=tmp_path / "app")


def test_update_rejects_package_without_app(ctx: AppContext, tmp_path: Path) -> None:
    priv, pub = keypair()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", b"x")
    manifest = publish(tmp_path, "99.0.0", buf.getvalue(), priv)
    with pytest.raises(IntegrityFailure, match="instalação válida"):
        selfmanage.update(ctx, str(manifest), trusted_keys=(pub,), target=tmp_path / "app")


def test_update_refuses_without_trusted_keys(ctx: AppContext, tmp_path: Path) -> None:
    priv, _ = keypair()
    manifest = publish(tmp_path, "99.0.0", b"x", priv)
    with pytest.raises(IntegrityFailure, match="nenhuma chave"):
        selfmanage.update(ctx, str(manifest), trusted_keys=(), target=tmp_path / "t")


def test_update_refuses_bad_signature(ctx: AppContext, tmp_path: Path) -> None:
    priv, _ = keypair()
    _, other_pub = keypair()
    manifest = publish(tmp_path, "99.0.0", b"x", priv)
    with pytest.raises(IntegrityFailure, match="assinatura"):
        selfmanage.update(ctx, str(manifest), trusted_keys=(other_pub,), target=tmp_path / "t")
    assert [e.type for e in ctx.events.iter_all()][-1] == "nexo.update.rejected"


def test_update_refuses_bad_checksum(ctx: AppContext, tmp_path: Path) -> None:
    priv, pub = keypair()
    manifest = publish(tmp_path, "99.0.0", b"x", priv, sha="0" * 64)
    with pytest.raises(IntegrityFailure, match="checksum"):
        selfmanage.update(ctx, str(manifest), trusted_keys=(pub,), target=tmp_path / "t")


def test_update_noop_when_current(ctx: AppContext, tmp_path: Path) -> None:
    priv, pub = keypair()
    manifest = publish(tmp_path, __version__, b"x", priv)
    assert selfmanage.update(ctx, str(manifest), trusted_keys=(pub,))["updated"] is False


def test_update_package_mode_reports_available(ctx: AppContext, tmp_path: Path) -> None:
    priv, pub = keypair()
    manifest = publish(tmp_path, "99.0.0", b"x", priv)
    result = selfmanage.update(ctx, str(manifest), trusted_keys=(pub,))
    assert result["package_mode"] and result["available"] == "99.0.0"


def test_rejects_insecure_scheme_and_bad_manifest(ctx: AppContext, tmp_path: Path) -> None:
    with pytest.raises(NexoError, match="recusada"):
        selfmanage.update(ctx, "http://example.com/manifest.json")
    bad = tmp_path / "m.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(NexoError, match="manifesto"):
        selfmanage.update(ctx, str(bad))


def test_parse_version() -> None:
    assert selfmanage.parse_version("1.2.3-beta") == (1, 2, 3)
    with pytest.raises(NexoError):
        selfmanage.parse_version("x.y")


def test_install_and_uninstall_package_mode(ctx: AppContext) -> None:
    assert selfmanage.install_mode() == "package"
    assert selfmanage.install(ctx) is None
    assert (ctx.home / "nexo.db").is_file()
    with pytest.raises(NexoError):
        selfmanage.install(ctx, to=ctx.home / "bin")
    result = selfmanage.uninstall(ctx, purge=True)
    assert result["purged"] and not ctx.home.exists()


def test_purge_refuses_home_dir(tmp_path: Path) -> None:
    unsafe = AppContext(cwd=tmp_path, home=Path.home())
    assert not selfmanage._safe_to_purge(unsafe.home)
    assert selfmanage._safe_to_purge(tmp_path / "x" / ".nexo")
