"""render_config: the CouchDB ini and LiveSync settings for Docker Compose."""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import render_config as rc


def pub(curve=ec.SECP256R1()):
    return ec.generate_private_key(curve).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def env(tmp_path, **kw):
    base = {"OUT_DIR": str(tmp_path / "out"), "DEVICES_DIR": str(tmp_path / "dev"), "COUCHDB_UUID": "a" * 32,
            "LIVESYNC_PASSWORD": "pw", "E2E_PASSPHRASE": "a long enough passphrase"}
    (tmp_path / "dev").mkdir(exist_ok=True)
    return {**base, **kw}


def test_renders_keys_and_settings(tmp_path):
    e = env(tmp_path)
    (tmp_path / "dev" / "phone.pub").write_text(pub())
    (tmp_path / "dev" / "mac.pub").write_text(pub())
    rc.main(e)
    ini = (tmp_path / "out/couchdb/10-agent-notes.ini").read_text()
    assert "uuid = " + "a" * 32 in ini
    keys = [l for l in ini.splitlines() if l.startswith("ec:")]
    assert [k.split(" = ")[0] for k in keys] == ["ec:mac", "ec:phone"]
    assert all("\\n" in k and "\n" not in k for k in keys)
    assert "${" not in ini
    s = json.loads((tmp_path / "out/livesync/settings.json").read_text())
    assert s["encrypt"] and s["usePathObfuscation"] and s["couchDB_DBNAME"] == "notes"
    assert oct((tmp_path / "out/livesync/settings.json").stat().st_mode & 0o777) == "0o640"


@pytest.mark.parametrize("name,content,why", [
    ("Bad_Name.pub", None, "device names"),
    ("rsa.pub", "rsa", "ES256"),
    ("p384.pub", "p384", "ES256"),
])
def test_refuses_bad_device_keys(tmp_path, name, content, why):
    e = env(tmp_path)
    from cryptography.hazmat.primitives.asymmetric import rsa
    text = {
        None: pub(),
        "rsa": rsa.generate_private_key(65537, 2048).public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
        "p384": pub(ec.SECP384R1()),
    }[content]
    (tmp_path / "dev" / name).write_text(text)
    with pytest.raises(SystemExit, match=why):
        rc.main(e)


@pytest.mark.parametrize("override,why", [
    ({"COUCHDB_UUID": "not-hex"}, "COUCHDB_UUID"),
    ({"COUCHDB_DATABASE": "_users"}, "COUCHDB_DATABASE"),
    ({"E2E_PASSPHRASE": "short"}, "E2E_PASSPHRASE"),
])
def test_refuses_bad_settings(tmp_path, override, why):
    with pytest.raises(SystemExit, match=why):
        rc.main(env(tmp_path, **override))


def test_web_email_file(tmp_path):
    rc.main(env(tmp_path, WEB_ALLOWED_EMAIL="me@example.com"))
    assert (tmp_path / "out/web/emails").read_text() == "me@example.com\n"
    with pytest.raises(SystemExit, match="WEB_ALLOWED_EMAIL"):
        rc.main(env(tmp_path, WEB_ALLOWED_EMAIL="a@b.c,evil@x.y"))
