# test/pop3server_test.py
"""
Pruebas unitarias para pop3server.py
Uso: python -m pytest test/pop3server_test.py -v
"""

import os
import sys
import json
import time
import socket
import threading
import pytest

from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from user.pop3server import (
    POP3Mailbox,
    UserAuth,
    POP3Protocol,
    POP3Factory,
)


# ===========================================================================
# Helpers
# ===========================================================================

def make_eml(directory: str, filename: str, content: bytes) -> str:
    """Crea un .eml y su .json de metadatos en el directorio dado."""
    eml_path  = os.path.join(directory, filename)
    meta_path = eml_path.replace(".eml", ".json")

    with open(eml_path, "wb") as f:
        f.write(content)

    meta = {
        "from":      "sender@ext.com",
        "to":        "alice@local.dev",
        "timestamp": filename.replace(".eml", ""),
        "read":      False,
        "path":      eml_path,
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    return eml_path


def make_users_json(storage_path: str, users: dict) -> str:
    path = os.path.join(storage_path, "users.json")
    with open(path, "w") as f:
        json.dump(users, f)
    return path


def make_protocol(storage_path: str, users: dict = None) -> POP3Protocol:
    """Crea un POP3Protocol listo para usar en tests unitarios."""
    if users is None:
        users = {"alice": "password123"}
    make_users_json(storage_path, users)

    auth     = UserAuth(storage_path)
    proto    = POP3Protocol(storage_path, auth)
    proto.transport = MagicMock()

    # Capturar líneas enviadas
    proto._sent = []
    proto.sendLine = lambda line: proto._sent.append(line)

    return proto


def sent_lines(proto: POP3Protocol) -> list[str]:
    """Decodifica las líneas enviadas por el protocolo."""
    return [l.decode() if isinstance(l, bytes) else l for l in proto._sent]


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def user_dir(tmp_path):
    d = tmp_path / "alice"
    d.mkdir()
    return str(d)


@pytest.fixture
def storage_path(tmp_path):
    return str(tmp_path)


@pytest.fixture
def mailbox_vacio(user_dir):
    return POP3Mailbox(user_dir)


@pytest.fixture
def mailbox_con_mensajes(user_dir):
    make_eml(user_dir, "msg001.eml", b"Subject: Uno\r\n\r\nCuerpo uno")
    time.sleep(0.01)
    make_eml(user_dir, "msg002.eml", b"Subject: Dos\r\n\r\nCuerpo dos")
    return POP3Mailbox(user_dir)


@pytest.fixture
def proto(tmp_path):
    storage = str(tmp_path)
    user_dir = tmp_path / "alice"
    user_dir.mkdir()
    make_eml(str(user_dir), "msg001.eml", b"Subject: Test\r\n\r\nHola mundo")
    make_eml(str(user_dir), "msg002.eml", b"Subject: Dos\r\n\r\nSegundo mensaje")
    return make_protocol(storage)


@pytest.fixture
def proto_autenticado(proto):
    """Protocolo ya en estado TRANSACTION."""
    proto.lineReceived(b"USER alice")
    proto.lineReceived(b"PASS password123")
    proto._sent.clear()
    return proto


# ===========================================================================
# POP3Mailbox
# ===========================================================================

class TestPOP3Mailbox:

    def test_buzón_vacio_stat(self, mailbox_vacio):
        count, size = mailbox_vacio.stat()
        assert count == 0
        assert size  == 0

    def test_carga_mensajes_existentes(self, mailbox_con_mensajes):
        count, _ = mailbox_con_mensajes.stat()
        assert count == 2

    def test_stat_retorna_tamaño_total(self, mailbox_con_mensajes):
        _, size = mailbox_con_mensajes.stat()
        assert size > 0

    def test_list_messages_retorna_pares(self, mailbox_con_mensajes):
        msgs = mailbox_con_mensajes.list_messages()
        assert len(msgs) == 2
        assert all(len(m) == 2 for m in msgs)

    def test_list_messages_numeracion_base1(self, mailbox_con_mensajes):
        msgs = mailbox_con_mensajes.list_messages()
        assert msgs[0][0] == 1
        assert msgs[1][0] == 2

    def test_get_message_retorna_bytes(self, mailbox_con_mensajes):
        data = mailbox_con_mensajes.get_message(1)
        assert isinstance(data, bytes)
        assert b"Subject: Uno" in data

    def test_get_message_numero_invalido(self, mailbox_con_mensajes):
        assert mailbox_con_mensajes.get_message(99) is None

    def test_get_message_base1(self, mailbox_con_mensajes):
        assert mailbox_con_mensajes.get_message(0) is None

    def test_delete_message_marca_borrado(self, mailbox_con_mensajes):
        assert mailbox_con_mensajes.delete_message(1) is True
        count, _ = mailbox_con_mensajes.stat()
        assert count == 1

    def test_delete_message_numero_invalido(self, mailbox_con_mensajes):
        assert mailbox_con_mensajes.delete_message(99) is False

    def test_delete_message_ya_borrado(self, mailbox_con_mensajes):
        mailbox_con_mensajes.delete_message(1)
        assert mailbox_con_mensajes.delete_message(1) is False

    def test_rollback_deshace_borrados(self, mailbox_con_mensajes):
        mailbox_con_mensajes.delete_message(1)
        mailbox_con_mensajes.rollback_deletes()
        count, _ = mailbox_con_mensajes.stat()
        assert count == 2

    def test_commit_borra_archivos(self, mailbox_con_mensajes, user_dir):
        eml_path = mailbox_con_mensajes._messages[0]["path_eml"]
        mailbox_con_mensajes.delete_message(1)
        mailbox_con_mensajes.commit_deletes()
        assert not os.path.exists(eml_path)

    def test_commit_borra_json(self, mailbox_con_mensajes, user_dir):
        meta_path = mailbox_con_mensajes._messages[0]["path_meta"]
        mailbox_con_mensajes.delete_message(1)
        mailbox_con_mensajes.commit_deletes()
        assert not os.path.exists(meta_path)

    def test_uidl_retorna_lista(self, mailbox_con_mensajes):
        result = mailbox_con_mensajes.uidl()
        assert len(result) == 2

    def test_uidl_uid_es_string(self, mailbox_con_mensajes):
        result = mailbox_con_mensajes.uidl()
        assert all(isinstance(uid, str) for _, uid in result)

    def test_uidl_numero_especifico(self, mailbox_con_mensajes):
        result = mailbox_con_mensajes.uidl(1)
        assert len(result) == 1
        assert result[0][0] == 1

    def test_list_excluye_borrados(self, mailbox_con_mensajes):
        mailbox_con_mensajes.delete_message(1)
        msgs = mailbox_con_mensajes.list_messages()
        assert len(msgs) == 1


# ===========================================================================
# UserAuth
# ===========================================================================

class TestUserAuth:

    def test_valida_credenciales_correctas(self, storage_path):
        make_users_json(storage_path, {"alice": "pass123"})
        auth = UserAuth(storage_path)
        assert auth.validate("alice", "pass123") is True

    def test_rechaza_contraseña_incorrecta(self, storage_path):
        make_users_json(storage_path, {"alice": "pass123"})
        auth = UserAuth(storage_path)
        assert auth.validate("alice", "incorrecta") is False

    def test_rechaza_usuario_inexistente(self, storage_path):
        make_users_json(storage_path, {"alice": "pass123"})
        auth = UserAuth(storage_path)
        assert auth.validate("noexiste", "pass123") is False

    def test_user_exists_true(self, storage_path):
        make_users_json(storage_path, {"alice": "pass123"})
        auth = UserAuth(storage_path)
        assert auth.user_exists("alice") is True

    def test_user_exists_false(self, storage_path):
        make_users_json(storage_path, {"alice": "pass123"})
        auth = UserAuth(storage_path)
        assert auth.user_exists("bob") is False

    def test_sin_users_json_no_valida(self, storage_path):
        auth = UserAuth(storage_path)
        assert auth.validate("alice", "pass") is False

    def test_multiples_usuarios(self, storage_path):
        make_users_json(storage_path, {"alice": "a", "bob": "b"})
        auth = UserAuth(storage_path)
        assert auth.validate("alice", "a") is True
        assert auth.validate("bob",   "b") is True


# ===========================================================================
# POP3Protocol — estado AUTHORIZATION
# ===========================================================================

class TestPOP3ProtocolAuth:

    def test_conexion_envia_ok(self, proto):
        proto.connectionMade()
        assert any("+OK" in l for l in sent_lines(proto))

    def test_user_correcto_responde_ok(self, proto):
        proto.connectionMade()
        proto._sent.clear()
        proto.lineReceived(b"USER alice")
        assert any("+OK" in l for l in sent_lines(proto))

    def test_user_sin_argumento_responde_err(self, proto):
        proto.connectionMade()
        proto._sent.clear()
        proto.lineReceived(b"USER")
        assert any("-ERR" in l for l in sent_lines(proto))

    def test_pass_correcto_autentica(self, proto):
        proto.connectionMade()
        proto.lineReceived(b"USER alice")
        proto._sent.clear()
        proto.lineReceived(b"PASS password123")
        assert any("+OK" in l for l in sent_lines(proto))
        assert proto.state == POP3Protocol.STATE_TRANSACTION

    def test_pass_incorrecto_rechaza(self, proto):
        proto.connectionMade()
        proto.lineReceived(b"USER alice")
        proto._sent.clear()
        proto.lineReceived(b"PASS incorrecta")
        assert any("-ERR" in l for l in sent_lines(proto))
        assert proto.state == POP3Protocol.STATE_AUTH

    def test_pass_sin_user_previo_err(self, proto):
        proto.connectionMade()
        proto._sent.clear()
        proto.lineReceived(b"PASS password123")
        assert any("-ERR" in l for l in sent_lines(proto))

    def test_comando_desconocido_err(self, proto):
        proto.connectionMade()
        proto._sent.clear()
        proto.lineReceived(b"HOLA")
        assert any("-ERR" in l for l in sent_lines(proto))

    def test_stat_antes_de_autenticar_err(self, proto):
        proto.connectionMade()
        proto._sent.clear()
        proto.lineReceived(b"STAT")
        assert any("-ERR" in l for l in sent_lines(proto))


# ===========================================================================
# POP3Protocol — estado TRANSACTION
# ===========================================================================

class TestPOP3ProtocolTransaction:

    def test_stat_retorna_conteo(self, proto_autenticado):
        proto_autenticado.lineReceived(b"STAT")
        lines = sent_lines(proto_autenticado)
        assert any("+OK 2" in l for l in lines)

    def test_list_retorna_mensajes(self, proto_autenticado):
        proto_autenticado.lineReceived(b"LIST")
        lines = sent_lines(proto_autenticado)
        assert any("+OK" in l for l in lines)
        assert any("1 " in l for l in lines)

    def test_list_numero_especifico(self, proto_autenticado):
        proto_autenticado.lineReceived(b"LIST 1")
        lines = sent_lines(proto_autenticado)
        assert any("+OK 1" in l for l in lines)

    def test_list_numero_invalido_err(self, proto_autenticado):
        proto_autenticado.lineReceived(b"LIST 99")
        lines = sent_lines(proto_autenticado)
        assert any("-ERR" in l for l in lines)

    def test_retr_retorna_mensaje(self, proto_autenticado):
        proto_autenticado.lineReceived(b"RETR 1")
        lines = sent_lines(proto_autenticado)
        assert any("+OK" in l for l in lines)
        assert any(b"Subject: Test" in l if isinstance(l, bytes) else "Subject: Test" in l
                   for l in proto_autenticado._sent)

    def test_retr_numero_invalido_err(self, proto_autenticado):
        proto_autenticado.lineReceived(b"RETR 99")
        lines = sent_lines(proto_autenticado)
        assert any("-ERR" in l for l in lines)

    def test_dele_marca_borrado(self, proto_autenticado):
        proto_autenticado.lineReceived(b"DELE 1")
        lines = sent_lines(proto_autenticado)
        assert any("+OK" in l for l in lines)

    def test_dele_numero_invalido_err(self, proto_autenticado):
        proto_autenticado.lineReceived(b"DELE 99")
        lines = sent_lines(proto_autenticado)
        assert any("-ERR" in l for l in lines)

    def test_rset_deshace_borrados(self, proto_autenticado):
        proto_autenticado.lineReceived(b"DELE 1")
        proto_autenticado._sent.clear()
        proto_autenticado.lineReceived(b"RSET")
        lines = sent_lines(proto_autenticado)
        assert any("+OK" in l for l in lines)
        # Verificar que el mensaje volvió
        proto_autenticado._sent.clear()
        proto_autenticado.lineReceived(b"STAT")
        assert any("+OK 2" in l for l in sent_lines(proto_autenticado))

    def test_noop_responde_ok(self, proto_autenticado):
        proto_autenticado.lineReceived(b"NOOP")
        assert any("+OK" in l for l in sent_lines(proto_autenticado))

    def test_uidl_retorna_lista(self, proto_autenticado):
        proto_autenticado.lineReceived(b"UIDL")
        lines = sent_lines(proto_autenticado)
        assert any("+OK" in l for l in lines)

    def test_uidl_numero_especifico(self, proto_autenticado):
        proto_autenticado.lineReceived(b"UIDL 1")
        lines = sent_lines(proto_autenticado)
        assert any("+OK" in l for l in lines)

    def test_top_retorna_encabezados(self, proto_autenticado):
        proto_autenticado.lineReceived(b"TOP 1 0")
        lines = sent_lines(proto_autenticado)
        assert any("+OK" in l for l in lines)
        assert any("Subject" in l for l in lines)

    def test_top_numero_invalido_err(self, proto_autenticado):
        proto_autenticado.lineReceived(b"TOP 99 0")
        lines = sent_lines(proto_autenticado)
        assert any("-ERR" in l for l in lines)

    def test_top_argumentos_faltantes_err(self, proto_autenticado):
        proto_autenticado.lineReceived(b"TOP 1")
        lines = sent_lines(proto_autenticado)
        assert any("-ERR" in l for l in lines)

    def test_quit_hace_commit_y_cierra(self, proto_autenticado):
        proto_autenticado.lineReceived(b"DELE 1")
        proto_autenticado._sent.clear()
        proto_autenticado.lineReceived(b"QUIT")
        lines = sent_lines(proto_autenticado)
        assert any("+OK" in l for l in lines)
        assert proto_autenticado.state == POP3Protocol.STATE_UPDATE
        proto_autenticado.transport.loseConnection.assert_called_once()

    def test_connection_lost_hace_rollback(self, proto_autenticado):
        proto_autenticado.lineReceived(b"DELE 1")
        proto_autenticado.connectionLost(None)
        # Rollback → mailbox vuelve a tener 2 mensajes
        count, _ = proto_autenticado.mailbox.stat()
        assert count == 2


# ===========================================================================
# POP3Factory
# ===========================================================================

class TestPOP3Factory:

    def test_build_protocol_retorna_pop3protocol(self, storage_path):
        make_users_json(storage_path, {"alice": "pass"})
        factory = POP3Factory(storage_path)
        proto   = factory.buildProtocol(MagicMock())
        assert isinstance(proto, POP3Protocol)

    def test_factory_comparte_auth(self, storage_path):
        make_users_json(storage_path, {"alice": "pass"})
        factory = POP3Factory(storage_path)
        p1 = factory.buildProtocol(MagicMock())
        p2 = factory.buildProtocol(MagicMock())
        assert p1.auth is p2.auth


# ===========================================================================
# Integración: servidor real + cliente socket
# ===========================================================================

@pytest.fixture(scope="module")
def servidor_pop3(tmp_path_factory):
    from twisted.internet import reactor

    tmp_path = tmp_path_factory.mktemp("pop3_integration")
    storage  = str(tmp_path)

    # Crear usuario y correos de prueba
    make_users_json(storage, {"alice": "password123"})
    user_dir = tmp_path / "alice"
    user_dir.mkdir()
    make_eml(str(user_dir), "msg001.eml", "Subject: Integración\r\n\r\nHola".encode("utf-8"))

    factory  = POP3Factory(storage)
    PORT     = 9110
    port_obj = reactor.listenTCP(PORT, factory)

    hilo = threading.Thread(
        target=reactor.run,
        kwargs={"installSignalHandlers": False},
        daemon=True,
    )
    hilo.start()
    time.sleep(0.3)

    yield storage, PORT

    port_obj.stopListening()


def pop3_exchange(port: int, commands: list[str]) -> list[str]:
    """Abre una conexión, envía comandos y retorna las respuestas."""
    responses = []
    with socket.create_connection(("localhost", port), timeout=5) as s:
        f = s.makefile("rb")
        responses.append(f.readline().decode().strip())  # banner
        for cmd in commands:
            s.sendall((cmd + "\r\n").encode())
            responses.append(f.readline().decode().strip())
    return responses


class TestIntegracionPOP3:

    def test_banner_ok(self, servidor_pop3):
        _, port = servidor_pop3
        r = pop3_exchange(port, [])
        assert r[0].startswith("+OK")

    def test_autenticacion_exitosa(self, servidor_pop3):
        _, port = servidor_pop3
        r = pop3_exchange(port, ["USER alice", "PASS password123"])
        assert r[1].startswith("+OK")   # USER
        assert r[2].startswith("+OK")   # PASS

    def test_autenticacion_fallida(self, servidor_pop3):
        _, port = servidor_pop3
        r = pop3_exchange(port, ["USER alice", "PASS incorrecta"])
        assert r[2].startswith("-ERR")

    def test_stat_tras_autenticacion(self, servidor_pop3):
        _, port = servidor_pop3
        r = pop3_exchange(port, ["USER alice", "PASS password123", "STAT"])
        assert r[3].startswith("+OK")

    def test_quit_responde_ok(self, servidor_pop3):
        _, port = servidor_pop3
        r = pop3_exchange(port, ["USER alice", "PASS password123", "QUIT"])
        assert r[3].startswith("+OK")