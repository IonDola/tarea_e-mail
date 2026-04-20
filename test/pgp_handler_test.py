# test/pgp_handler_test.py
"""
Pruebas unitarias para pgp_handler.py
Uso: python -m pytest test/pgp_handler_test.py -v

Requisitos:
    pip install python-gnupg pytest
    sudo apt install gnupg2
"""

import os
import sys
import pytest

from email.mime.multipart import MIMEMultipart

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from security.pgp_handler import (
    PGPKeyManager,
    PGPEncryptor,
    PGPSigner,
    PGPMIMEBuilder,
    PGPHandler,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(scope="module")
def keys_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("keys"))


@pytest.fixture(scope="module")
def key_manager(keys_dir):
    return PGPKeyManager(keys_dir)


@pytest.fixture(scope="module")
def generated_keys(key_manager):
    """
    Genera llaves para Alice y Bob una sola vez para todo el módulo.
    Scope module evita regenerar llaves en cada test (operación costosa).
    """
    fp_alice = key_manager.generate_key(
        name       = "Alice Test",
        email      = "alice@test.local",
        passphrase = "alice_pass",
    )
    fp_bob = key_manager.generate_key(
        name       = "Bob Test",
        email      = "bob@test.local",
        passphrase = "bob_pass",
    )
    return {"alice": fp_alice, "bob": fp_bob}


@pytest.fixture(scope="module")
def encryptor(key_manager, generated_keys):
    return PGPEncryptor(key_manager)


@pytest.fixture(scope="module")
def signer(key_manager, generated_keys):
    return PGPSigner(key_manager)


@pytest.fixture(scope="module")
def mime_builder(key_manager, generated_keys):
    return PGPMIMEBuilder(key_manager)


@pytest.fixture(scope="module")
def handler(keys_dir, generated_keys):
    return PGPHandler(keys_dir)


# ===========================================================================
# PGPKeyManager
# ===========================================================================

class TestPGPKeyManager:

    def test_crea_directorio_keys(self, keys_dir):
        assert os.path.isdir(keys_dir)

    def test_generate_key_retorna_fingerprint(self, generated_keys):
        assert len(generated_keys["alice"]) == 40
        assert len(generated_keys["bob"])   == 40

    def test_fingerprints_distintos(self, generated_keys):
        assert generated_keys["alice"] != generated_keys["bob"]

    def test_key_exists_true(self, key_manager, generated_keys):
        assert key_manager.key_exists("alice@test.local") is True

    def test_key_exists_false(self, key_manager):
        assert key_manager.key_exists("nadie@noexiste.com") is False

    def test_list_keys_incluye_generadas(self, key_manager, generated_keys):
        keys = key_manager.list_keys()
        fingerprints = [k["fingerprint"] for k in keys]
        assert generated_keys["alice"] in fingerprints
        assert generated_keys["bob"]   in fingerprints

    def test_export_public_key_retorna_string(self, key_manager):
        key = key_manager.export_public_key("alice@test.local")
        assert "BEGIN PGP PUBLIC KEY BLOCK" in key

    def test_export_public_key_email_inexistente(self, key_manager):
        with pytest.raises(ValueError):
            key_manager.export_public_key("nadie@noexiste.com")

    def test_save_public_key_crea_archivo(self, key_manager, keys_dir):
        path = key_manager.save_public_key("alice@test.local")
        assert os.path.exists(path)
        assert path.endswith(".asc")

    def test_import_key_desde_exportada(self, key_manager):
        # Exportar llave de Alice e importarla de nuevo
        pub_key = key_manager.export_public_key("alice@test.local")
        fps     = key_manager.import_key(pub_key)
        assert len(fps) > 0

    def test_import_key_invalida_lanza_error(self, key_manager):
        with pytest.raises(ValueError):
            key_manager.import_key("esto no es una llave pgp")

    def test_import_key_from_file(self, key_manager, keys_dir):
        path = key_manager.save_public_key("bob@test.local")
        fps  = key_manager.import_key_from_file(path)
        assert len(fps) > 0

    def test_import_key_from_file_inexistente(self, key_manager):
        with pytest.raises(FileNotFoundError):
            key_manager.import_key_from_file("no_existe.asc")


# ===========================================================================
# PGPEncryptor
# ===========================================================================

class TestPGPEncryptor:

    def test_encrypt_retorna_string_armored(self, encryptor):
        result = encryptor.encrypt("Hola mundo", ["alice@test.local"])
        assert "BEGIN PGP MESSAGE" in result

    def test_decrypt_retorna_texto_original(self, encryptor):
        texto     = "Mensaje secreto de prueba"
        cifrado   = encryptor.encrypt(texto, ["alice@test.local"])
        descifrado = encryptor.decrypt(cifrado, passphrase="alice_pass")
        assert texto in descifrado

    def test_encrypt_multiples_destinatarios(self, encryptor):
        texto   = "Para Alice y Bob"
        cifrado = encryptor.encrypt(
            texto,
            ["alice@test.local", "bob@test.local"]
        )
        # Ambos deben poder descifrarlo
        dec_alice = encryptor.decrypt(cifrado, passphrase="alice_pass")
        dec_bob   = encryptor.decrypt(cifrado, passphrase="bob_pass")
        assert texto in dec_alice
        assert texto in dec_bob

    def test_decrypt_passphrase_incorrecta_lanza_error(self, encryptor):
        cifrado = encryptor.encrypt("Texto", ["alice@test.local"])
        with pytest.raises(RuntimeError):
            encryptor.decrypt(cifrado, passphrase="incorrecta")

    def test_decrypt_datos_invalidos_lanza_error(self, encryptor):
        with pytest.raises(RuntimeError):
            encryptor.decrypt("esto no es pgp", passphrase="alice_pass")

    def test_encrypt_con_firma(self, encryptor):
        result = encryptor.encrypt(
            "Texto firmado y cifrado",
            recipients = ["bob@test.local"],
            passphrase = "alice_pass",
            sign       = "alice@test.local",
        )
        assert "BEGIN PGP MESSAGE" in result

    def test_encrypt_file(self, encryptor, tmp_path):
        archivo = tmp_path / "secreto.txt"
        archivo.write_text("Contenido secreto del archivo")
        cifrado_path = encryptor.encrypt_file(
            str(archivo),
            ["alice@test.local"]
        )
        assert os.path.exists(cifrado_path)
        assert cifrado_path.endswith(".gpg")

    def test_encrypt_file_no_existente(self, encryptor):
        with pytest.raises(FileNotFoundError):
            encryptor.encrypt_file("no_existe.txt", ["alice@test.local"])

    def test_decrypt_file(self, encryptor, tmp_path):
        # Crear y cifrar archivo
        archivo = tmp_path / "doc.txt"
        archivo.write_text("Contenido a cifrar")
        cifrado_path  = encryptor.encrypt_file(str(archivo), ["alice@test.local"])
        descifrado_path = encryptor.decrypt_file(
            cifrado_path,
            passphrase  = "alice_pass",
            output_path = str(tmp_path / "doc.dec"),
        )
        assert os.path.exists(descifrado_path)
        with open(descifrado_path) as f:
            assert "Contenido a cifrar" in f.read()

    def test_decrypt_file_no_existente(self, encryptor):
        with pytest.raises(FileNotFoundError):
            encryptor.decrypt_file("no_existe.gpg")


# ===========================================================================
# PGPSigner
# ===========================================================================

class TestPGPSigner:

    def test_sign_retorna_firma_armored(self, signer):
        firma = signer.sign(
            "Mensaje a firmar",
            signer     = "alice@test.local",
            passphrase = "alice_pass",
        )
        assert "BEGIN PGP SIGNATURE" in firma

    def test_verify_firma_valida(self, signer):
        data  = "Mensaje original"
        firma = signer.sign(data, "alice@test.local", "alice_pass")
        result = signer.verify(data, firma)
        assert result["valid"] is True

    def test_verify_retorna_fingerprint(self, signer, generated_keys):
        data  = "Mensaje"
        firma = signer.sign(data, "alice@test.local", "alice_pass")
        result = signer.verify(data, firma)
        assert result["fingerprint"] != ""

    def test_verify_firma_invalida(self, signer):
        data   = "Mensaje original"
        firma  = signer.sign(data, "alice@test.local", "alice_pass")
        result = signer.verify("Mensaje alterado", firma)
        assert result["valid"] is False

    def test_verify_firma_datos_vacios(self, signer):
        result = signer.verify("datos", "firma_invalida")
        assert result["valid"] is False

    def test_sign_passphrase_incorrecta_lanza_error(self, signer):
        with pytest.raises(RuntimeError):
            signer.sign(
                "Texto",
                signer     = "alice@test.local",
                passphrase = "incorrecta",
            )

    def test_sign_inline(self, signer):
        firma = signer.sign(
            "Mensaje inline",
            signer     = "alice@test.local",
            passphrase = "alice_pass",
            detached   = False,
        )
        assert "BEGIN PGP MESSAGE" in firma


# ===========================================================================
# PGPMIMEBuilder
# ===========================================================================

class TestPGPMIMEBuilder:

    def test_build_signed_retorna_mime(self, mime_builder):
        msg = mime_builder.build_signed(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Mensaje firmado",
            body       = "Cuerpo del mensaje",
            passphrase = "alice_pass",
        )
        assert isinstance(msg, MIMEMultipart)

    def test_build_signed_content_type(self, mime_builder):
        msg = mime_builder.build_signed(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Test",
            body       = "Cuerpo",
            passphrase = "alice_pass",
        )
        assert "signed" in msg.get_content_type()

    def test_build_signed_tiene_dos_partes(self, mime_builder):
        msg = mime_builder.build_signed(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Test",
            body       = "Cuerpo",
            passphrase = "alice_pass",
        )
        assert len(msg.get_payload()) == 2

    def test_build_signed_cuerpo_legible(self, mime_builder):
        body = "Este cuerpo es legible sin PGP"
        msg  = mime_builder.build_signed(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Test",
            body       = body,
            passphrase = "alice_pass",
        )
        parte_cuerpo = msg.get_payload()[0]
        assert body in parte_cuerpo.get_payload()

    def test_build_encrypted_retorna_mime(self, mime_builder):
        msg = mime_builder.build_encrypted(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Mensaje cifrado",
            body       = "Cuerpo secreto",
        )
        assert isinstance(msg, MIMEMultipart)

    def test_build_encrypted_content_type(self, mime_builder):
        msg = mime_builder.build_encrypted(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Test",
            body       = "Cuerpo",
        )
        assert "encrypted" in msg.get_content_type()

    def test_build_encrypted_tiene_dos_partes(self, mime_builder):
        msg = mime_builder.build_encrypted(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Test",
            body       = "Cuerpo",
        )
        assert len(msg.get_payload()) == 2

    def test_decrypt_mime_retorna_texto_original(self, mime_builder):
        body = "Texto secreto para Bob"
        msg  = mime_builder.build_encrypted(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Test",
            body       = body,
        )
        resultado = mime_builder.decrypt_mime(msg, passphrase="bob_pass")
        assert body in resultado

    def test_decrypt_mime_mensaje_no_cifrado_lanza_error(self, mime_builder):
        msg = MIMEMultipart("alternative")
        with pytest.raises(ValueError):
            mime_builder.decrypt_mime(msg)

    def test_verify_mime_firma_valida(self, mime_builder):
        msg = mime_builder.build_signed(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Test",
            body       = "Cuerpo verificable",
            passphrase = "alice_pass",
        )
        result = mime_builder.verify_mime(msg)
        assert result["valid"] is True

    def test_verify_mime_mensaje_no_firmado_lanza_error(self, mime_builder):
        msg = MIMEMultipart("alternative")
        with pytest.raises(ValueError):
            mime_builder.verify_mime(msg)

    def test_build_encrypted_con_firma(self, mime_builder):
        msg = mime_builder.build_encrypted(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Cifrado y firmado",
            body       = "Mensaje seguro",
            passphrase = "alice_pass",
            sign       = True,
        )
        assert isinstance(msg, MIMEMultipart)
        resultado = mime_builder.decrypt_mime(msg, passphrase="bob_pass")
        assert "Mensaje seguro" in resultado


# ===========================================================================
# PGPHandler (fachada)
# ===========================================================================

class TestPGPHandler:

    def test_handler_tiene_key_manager(self, handler):
        assert isinstance(handler.key_manager, PGPKeyManager)

    def test_handler_tiene_encryptor(self, handler):
        assert isinstance(handler.encryptor, PGPEncryptor)

    def test_handler_tiene_signer(self, handler):
        assert isinstance(handler.signer, PGPSigner)

    def test_handler_tiene_mime(self, handler):
        assert isinstance(handler.mime, PGPMIMEBuilder)

    def test_flujo_completo_cifrar_descifrar(self, handler):
        texto   = "Flujo completo PGP"
        cifrado = handler.encryptor.encrypt(texto, ["alice@test.local"])
        result  = handler.encryptor.decrypt(cifrado, passphrase="alice_pass")
        assert texto in result

    def test_flujo_completo_firmar_verificar(self, handler):
        texto = "Flujo completo firma"
        firma = handler.signer.sign(texto, "alice@test.local", "alice_pass")
        result = handler.signer.verify(texto, firma)
        assert result["valid"] is True

    def test_flujo_completo_mime_cifrado(self, handler):
        msg = handler.mime.build_encrypted(
            sender     = "alice@test.local",
            recipients = ["bob@test.local"],
            subject    = "Test completo",
            body       = "Cuerpo del test",
        )
        resultado = handler.mime.decrypt_mime(msg, passphrase="bob_pass")
        assert "Cuerpo del test" in resultado