import os
import json
import pytest
import tempfile
import threading
import time
import smtplib

from unittest.mock import MagicMock, patch, call
from twisted.internet import defer
from twisted.mail import smtp as twisted_smtp
from twisted.mail.smtp import Address, User

# Ajustar path para importar desde smtp/
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from smtp.smtpserver import MaildirStorage, SMTPMessage, SMTPDelivery, SMTPFactory


## @package test.smtpserver_test
# Tests unitarios para smtpserver.py usando pytest y mocks.

# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def tmp_storage(tmp_path):
    """Directorio temporal limpio para cada test."""
    return MaildirStorage(str(tmp_path))


@pytest.fixture
def accepted_domains():
    return ["local.dev", "example.com"]


@pytest.fixture
def delivery(tmp_storage, accepted_domains):
    return SMTPDelivery(tmp_storage, accepted_domains)

@pytest.fixture(scope="module")
def servidor_smtp(tmp_path_factory):
    """
    Levanta el servidor UNA SOLA VEZ para todos los tests de integración.
    scope='module' evita reiniciar el reactor de Twisted.
    """
    from twisted.internet import reactor

    tmp_path = tmp_path_factory.mktemp("integration")
    storage  = MaildirStorage(str(tmp_path))
    factory  = SMTPFactory(storage, ["test.local"])

    PORT = 9025
    port_obj = reactor.listenTCP(PORT, factory)

    hilo = threading.Thread(
        target=reactor.run,
        kwargs={"installSignalHandlers": False},
        daemon=True,
    )
    hilo.start()
    time.sleep(0.3)

    yield tmp_path, PORT

    port_obj.stopListening()

# ===========================================================================
# MaildirStorage
# ===========================================================================

class TestMaildirStorage:
    """
    @note: Estos tests verifican que MaildirStorage crea los directorios y archivos correctos, y que el contenido de los archivos es el esperado. No prueban la lógica de generación de nombres únicos ni la concurrencia, que podrían ser temas para tests adicionales.
    """
    def test_crea_directorio_storage(self, tmp_path):
        path = str(tmp_path / "nuevo_dir")
        MaildirStorage(path)
        assert os.path.isdir(path)

    def test_crea_directorio_usuario(self, tmp_storage, tmp_path):
        tmp_storage.save("alice@local.dev", "bob@external.com", b"Subject: Test\r\nHola")
        assert os.path.isdir(str(tmp_path / "alice"))

    def test_guarda_archivo_eml(self, tmp_storage, tmp_path):
        tmp_storage.save("alice@local.dev", "bob@external.com", b"Subject: Test\r\nHola")
        emls = list((tmp_path / "alice").glob("*.eml"))
        assert len(emls) == 1

    def test_guarda_archivo_json(self, tmp_storage, tmp_path):
        tmp_storage.save("alice@local.dev", "bob@external.com", b"Subject: Test\r\nHola")
        jsons = list((tmp_path / "alice").glob("*.json"))
        assert len(jsons) == 1

    def test_contenido_eml_correcto(self, tmp_storage, tmp_path):
        raw = b"Subject: Hola\r\nMensaje de prueba"
        tmp_storage.save("alice@local.dev", "bob@external.com", raw)
        eml = list((tmp_path / "alice").glob("*.eml"))[0]
        assert eml.read_bytes() == raw

    def test_metadatos_json_correctos(self, tmp_storage, tmp_path):
        tmp_storage.save("alice@local.dev", "bob@external.com", b"Hola")
        meta_file = list((tmp_path / "alice").glob("*.json"))[0]
        meta = json.loads(meta_file.read_text())

        assert meta["from"]  == "bob@external.com"
        assert meta["to"]    == "alice@local.dev"
        assert meta["read"]  == False
        assert "timestamp"   in meta
        assert "path"        in meta

    def test_multiples_correos_mismo_usuario(self, tmp_storage, tmp_path):
        for i in range(3):
            time.sleep(0.01)   # evitar colisión de timestamp
            tmp_storage.save("alice@local.dev", f"sender{i}@ext.com", b"Msg")
        emls = list((tmp_path / "alice").glob("*.eml"))
        assert len(emls) == 3

    def test_usuarios_distintos_directorios_separados(self, tmp_storage, tmp_path):
        tmp_storage.save("alice@local.dev", "x@ext.com", b"A")
        tmp_storage.save("bob@local.dev",   "x@ext.com", b"B")
        assert os.path.isdir(str(tmp_path / "alice"))
        assert os.path.isdir(str(tmp_path / "bob"))


# ===========================================================================
# SMTPMessage
# ===========================================================================

class TestSMTPMessage:
    """
    @note: Estos tests verifican que SMTPMessage acumula las líneas recibidas, que al recibir EOM persiste el mensaje y limpia el buffer, y que al perder la conexión también limpia el buffer sin persistir. No prueban casos de error ni la concurrencia, que podrían ser temas para tests adicionales.
    """
    def test_acumula_lineas(self, tmp_storage):
        msg = SMTPMessage(tmp_storage, "alice@local.dev", "bob@ext.com")
        msg.lineReceived(b"Subject: Test")
        msg.lineReceived(b"Hola mundo")
        assert len(msg.lines) == 2

    def test_eom_persiste_y_limpia(self, tmp_storage, tmp_path):
        msg = SMTPMessage(tmp_storage, "alice@local.dev", "bob@ext.com")
        msg.lineReceived(b"Subject: Test")
        msg.lineReceived(b"Cuerpo")

        result = msg.eomReceived()
        assert isinstance(result, defer.Deferred)
        assert msg.lines == []

        emls = list((tmp_path / "alice").glob("*.eml"))
        assert len(emls) == 1

    def test_eom_concatena_con_crlf(self, tmp_storage, tmp_path):
        msg = SMTPMessage(tmp_storage, "alice@local.dev", "bob@ext.com")
        msg.lineReceived(b"linea1")
        msg.lineReceived(b"linea2")
        msg.eomReceived()

        eml = list((tmp_path / "alice").glob("*.eml"))[0]
        assert eml.read_bytes() == b"linea1\r\nlinea2"

    def test_connection_lost_limpia_buffer(self, tmp_storage):
        msg = SMTPMessage(tmp_storage, "alice@local.dev", "bob@ext.com")
        msg.lineReceived(b"Datos parciales")
        msg.connectionLost()
        assert msg.lines == []

    def test_connection_lost_no_persiste(self, tmp_storage, tmp_path):
        msg = SMTPMessage(tmp_storage, "alice@local.dev", "bob@ext.com")
        msg.lineReceived(b"Datos parciales")
        msg.connectionLost()
        assert not list((tmp_path / "alice").glob("*.eml"))


# ===========================================================================
# SMTPDelivery
# ===========================================================================

class TestSMTPDelivery:
    """
    @note: Estos tests verifican que SMTPDelivery valida correctamente los remitentes y destinatarios, y que crea instancias de SMTPMessage para entregar los mensajes. No prueban la lógica de generación de nombres únicos ni la concurrencia, que podrían ser temas para tests adicionales.
    """
    def _make_user(self, address_str):
        addr = Address(address_str.encode())
        return User(addr, None, None, addr)

    def test_validate_from_acepta_cualquier_remitente(self, delivery):
        origin = MagicMock()
        result = delivery.validateFrom(("localhost", b"127.0.0.1"), origin)
        assert result is origin

    def test_validate_from_guarda_sender(self, delivery):
        origin = MagicMock()
        origin.__str__ = lambda _: "bob@ext.com"
        delivery.validateFrom(("localhost", b"127.0.0.1"), origin)
        assert delivery._sender == "bob@ext.com"

    def test_validate_to_dominio_aceptado(self, delivery):
        user = self._make_user("alice@local.dev")
        result = delivery.validateTo(user)
        assert callable(result)

    def test_validate_to_dominio_aceptado_case_insensitive(self, delivery):
        user = self._make_user("alice@LOCAL.DEV")
        result = delivery.validateTo(user)
        assert callable(result)

    def test_validate_to_dominio_rechazado(self, delivery):
        user = self._make_user("alice@noexiste.com")
        with pytest.raises(twisted_smtp.SMTPBadRcpt):
            delivery.validateTo(user)

    def test_validate_to_sin_arroba_rechazado(self, delivery):
        user = self._make_user("alicesindominio")
        with pytest.raises(twisted_smtp.SMTPBadRcpt):
            delivery.validateTo(user)

    def test_validate_to_retorna_callable_que_produce_smtpmessage(self, delivery):
        from smtp.smtpserver import SMTPMessage
        user = self._make_user("alice@local.dev")
        factory_fn = delivery.validateTo(user)
        msg = factory_fn()
        assert isinstance(msg, SMTPMessage)

    def test_received_header_contiene_timestamp(self, delivery):
        header = delivery.receivedHeader(
            ("localhost", b"127.0.0.1"),
            MagicMock(),
            []
        )
        assert b"Received:" in header

    def test_multiples_dominios_aceptados(self, tmp_storage):
        delivery = SMTPDelivery(tmp_storage, ["alpha.com", "beta.org", "gamma.net"])
        for domain in ["alpha.com", "beta.org", "gamma.net"]:
            user = MagicMock()
            user.dest.__str__ = lambda _, d=domain: f"x@{d}"
            assert callable(delivery.validateTo(user))


# ===========================================================================
# SMTPFactory
# ===========================================================================

class TestSMTPFactory:
    """
    @note: Estos tests verifican que SMTPFactory construye protocolos ESMTP con la clase de entrega correcta, y que el contexto SSL se puede asignar. No prueban la lógica de manejo de conexiones ni la concurrencia, que podrían ser temas para tests adicionales.
    """
    def test_build_protocol_retorna_esmtp(self, tmp_storage, accepted_domains):
        factory = SMTPFactory(tmp_storage, accepted_domains)
        addr    = MagicMock()
        proto   = factory.buildProtocol(addr)
        assert isinstance(proto, twisted_smtp.ESMTP)

    def test_build_protocol_tiene_delivery(self, tmp_storage, accepted_domains):
        factory = SMTPFactory(tmp_storage, accepted_domains)
        proto   = factory.buildProtocol(MagicMock())
        assert isinstance(proto.delivery, SMTPDelivery)

    def test_ssl_context_none_por_defecto(self, tmp_storage, accepted_domains):
        factory = SMTPFactory(tmp_storage, accepted_domains)
        assert factory.ssl_context is None

    def test_ssl_context_se_puede_asignar(self, tmp_storage, accepted_domains):
        factory = SMTPFactory(tmp_storage, accepted_domains)
        ctx_mock = MagicMock()
        factory.ssl_context = ctx_mock
        assert factory.ssl_context is ctx_mock


# ===========================================================================
# Integración: servidor real en hilo + smtplib
# ===========================================================================

class TestIntegracion:
    """
    @note: Estos tests de integración verifican que el servidor SMTP puede recibir correos reales enviados por smtplib y que los persiste correctamente. Levanta el servidor SMTP en un hilo separado usando el reactor de Twisted,
    luego envía un correo real con smtplib y verifica que se persistió.
    """
    PORT = 9025

    def test_correo_llega_al_storage(self, servidor_smtp):
        tmp_path, port = servidor_smtp
        msg = "Subject: Integración\r\n\r\nHola desde smtplib".encode("utf-8")
        with smtplib.SMTP("localhost", port) as s:
            s.sendmail(
                "remitente@externa.com",
                "usuario@test.local",
                msg
            )
        time.sleep(0.2)
        emls = list((tmp_path / "usuario").glob("*.eml"))
        assert len(emls) == 1

    def test_dominio_rechazado_lanza_excepcion(self, servidor_smtp):
        _, port = servidor_smtp
        with smtplib.SMTP("localhost", port, timeout=5) as s:  # timeout evita congelarse
            msg = "Subject: Rechazado\r\n\r\nNo debería llegar".encode("utf-8")
            with pytest.raises(smtplib.SMTPRecipientsRefused):
                s.sendmail(
                    "remitente@ext.com",
                    "usuario@dominio-invalido.com",
                    msg
                )

    def test_multiples_destinatarios_validos(self, servidor_smtp):
        tmp_path, port = servidor_smtp
        with smtplib.SMTP("localhost", port) as s:
            for nombre in ["ana", "luis", "marta"]:
                time.sleep(0.05)
                s.sendmail(
                    "remitente@ext.com",
                    f"{nombre}@test.local",
                    f"Subject: Para {nombre}\r\n\r\nHola {nombre}"
                )
        time.sleep(0.3)
        for nombre in ["ana", "luis", "marta"]:
            emls = list((tmp_path / nombre).glob("*.eml"))
            assert len(emls) == 1, f"Falta correo para {nombre}"