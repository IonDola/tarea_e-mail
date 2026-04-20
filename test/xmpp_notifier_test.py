# test/xmpp_notifier_test.py
"""
Pruebas unitarias para xmpp_notifier.py
Uso: python -m pytest test/xmpp_notifier_test.py -v
"""

import os
import sys
import json
import time
import pytest

from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from xmpp.xmpp_notifier import (
    MailboxMonitor,
    NotificationFormatter,
    XMPPNotifierHandler,
    XMPPNotifierService,
    load_config,
)


# ===========================================================================
# Helpers
# ===========================================================================

def make_meta(
    directory:  str,
    filename:   str,
    sender:     str = "bob@ext.com",
    subject:    str = "Asunto de prueba",
    read:       bool = False,
) -> str:
    """Crea un .json de metadatos y su .eml vacío en el directorio dado."""
    eml_path  = os.path.join(directory, filename)
    meta_path = eml_path.replace(".eml", ".json")

    open(eml_path, "wb").close()   # .eml vacío

    meta = {
        "from":      sender,
        "to":        "alice@local.dev",
        "subject":   subject,
        "timestamp": filename.replace(".eml", ""),
        "read":      read,
        "path":      eml_path,
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    return meta_path


def make_config(storage_path: str, **overrides) -> dict:
    """Retorna un dict de configuración válido con valores por defecto."""
    base = {
        "jid":              "bot@jabber.org",
        "password":         "secreto",
        "recipient_jid":    "alice@jabber.org",
        "storage_path":     storage_path,
        "mail_user":        "alice",
        "host":             "jabber.org",
        "port":             5222,
        "interval_seconds": 30,
    }
    base.update(overrides)
    return base


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def user_dir(tmp_path):
    d = tmp_path / "alice"
    d.mkdir(exist_ok=True)
    return str(d)


@pytest.fixture
def storage_path(tmp_path):
    return str(tmp_path)


@pytest.fixture
def monitor(tmp_path):
    user_dir = tmp_path / "alice"
    user_dir.mkdir(exist_ok=True)
    return MailboxMonitor(str(tmp_path), "alice")


@pytest.fixture
def formatter():
    return NotificationFormatter()


@pytest.fixture
def handler():
    h = XMPPNotifierHandler("alice@jabber.org")
    h.xmlstream = MagicMock()
    h._ready    = True
    return h


@pytest.fixture
def service(tmp_path):
    config = make_config(str(tmp_path))
    user_dir = tmp_path / "alice"
    user_dir.mkdir(exist_ok=True)
    svc = XMPPNotifierService(config)
    # Reemplazar handler con mock para no necesitar servidor XMPP real
    svc.handler = MagicMock()
    svc.handler._ready    = True
    svc.handler.send_message = MagicMock(return_value=True)
    return svc


# ===========================================================================
# MailboxMonitor
# ===========================================================================

class TestMailboxMonitor:

    def test_directorio_inexistente_retorna_vacio(self, tmp_path):
        monitor = MailboxMonitor(str(tmp_path), "noexiste")
        assert monitor.get_unread() == []

    def test_sin_correos_retorna_vacio(self, monitor):
        assert monitor.get_unread() == []

    def test_detecta_correo_no_leido(self, monitor, user_dir):
        make_meta(user_dir, "msg001.eml")
        unread = monitor.get_unread()
        assert len(unread) == 1

    def test_ignora_correo_leido(self, monitor, user_dir):
        make_meta(user_dir, "msg001.eml", read=True)
        assert monitor.get_unread() == []

    def test_detecta_multiples_no_leidos(self, monitor, user_dir):
        make_meta(user_dir, "msg001.eml")
        make_meta(user_dir, "msg002.eml")
        assert len(monitor.get_unread()) == 2

    def test_no_repite_notificaciones(self, monitor, user_dir):
        make_meta(user_dir, "msg001.eml")
        monitor.get_unread()          # primera llamada — notifica
        assert monitor.get_unread() == []   # segunda — ya notificado

    def test_detecta_correo_nuevo_tras_primera_llamada(self, monitor, user_dir):
        make_meta(user_dir, "msg001.eml")
        monitor.get_unread()
        make_meta(user_dir, "msg002.eml")   # llega uno nuevo
        assert len(monitor.get_unread()) == 1

    def test_meta_contiene_campos_esperados(self, monitor, user_dir):
        make_meta(user_dir, "msg001.eml", sender="bob@ext.com", subject="Hola")
        unread = monitor.get_unread()
        assert unread[0]["from"]    == "bob@ext.com"
        assert unread[0]["subject"] == "Hola"

    def test_ignora_json_malformado(self, monitor, user_dir):
        bad = os.path.join(user_dir, "bad.json")
        with open(bad, "w") as f:
            f.write("{ esto no es json }")
        assert monitor.get_unread() == []

    def test_count_unread_cero_si_vacio(self, monitor):
        assert monitor.count_unread() == 0

    def test_count_unread_correcto(self, monitor, user_dir):
        make_meta(user_dir, "msg001.eml")
        make_meta(user_dir, "msg002.eml")
        assert monitor.count_unread() == 2

    def test_count_unread_ignora_leidos(self, monitor, user_dir):
        make_meta(user_dir, "msg001.eml", read=False)
        make_meta(user_dir, "msg002.eml", read=True)
        assert monitor.count_unread() == 1

    def test_count_unread_directorio_inexistente(self, tmp_path):
        monitor = MailboxMonitor(str(tmp_path), "fantasma")
        assert monitor.count_unread() == 0


# ===========================================================================
# NotificationFormatter
# ===========================================================================

class TestNotificationFormatter:

    def test_format_summary_singular(self, formatter):
        assert "1 correo" in formatter.format_summary(1)

    def test_format_summary_plural(self, formatter):
        result = formatter.format_summary(3)
        assert "3 correos" in result

    def test_format_single_incluye_remitente(self, formatter):
        meta   = {"from": "bob@ext.com", "subject": "Hola", "timestamp": "20260101"}
        result = formatter.format_single(meta)
        assert "bob@ext.com" in result

    def test_format_single_incluye_asunto(self, formatter):
        meta   = {"from": "bob@ext.com", "subject": "Mi asunto", "timestamp": ""}
        result = formatter.format_single(meta)
        assert "Mi asunto" in result

    def test_format_single_sin_asunto_usa_default(self, formatter):
        meta   = {"from": "bob@ext.com", "timestamp": ""}
        result = formatter.format_single(meta)
        assert "(sin asunto)" in result

    def test_format_single_sin_remitente_usa_default(self, formatter):
        meta   = {"subject": "Hola", "timestamp": ""}
        result = formatter.format_single(meta)
        assert "desconocido" in result

    def test_format_notification_incluye_resumen(self, formatter):
        unread = [{"from": "a@b.com", "subject": "X", "timestamp": ""}]
        result = formatter.format_notification(unread, 1)
        assert "1 correo" in result

    def test_format_notification_incluye_detalle_de_cada_correo(self, formatter):
        unread = [
            {"from": "a@b.com", "subject": "Primero",  "timestamp": ""},
            {"from": "c@d.com", "subject": "Segundo",  "timestamp": ""},
        ]
        result = formatter.format_notification(unread, 2)
        assert "Primero" in result
        assert "Segundo" in result

    def test_format_notification_multiples_lineas(self, formatter):
        unread = [
            {"from": "a@b.com", "subject": "A", "timestamp": ""},
            {"from": "c@d.com", "subject": "B", "timestamp": ""},
        ]
        result = formatter.format_notification(unread, 2)
        assert len(result.splitlines()) >= 3   # resumen + 2 detalles


# ===========================================================================
# XMPPNotifierHandler
# ===========================================================================

class TestXMPPNotifierHandler:

    def test_send_message_retorna_true_cuando_listo(self, handler):
        result = handler.send_message("Hola")
        assert result is True

    def test_send_message_llama_a_xmlstream_send(self, handler):
        handler.send_message("Hola")
        handler.xmlstream.send.assert_called_once()

    def test_send_message_retorna_false_si_no_listo(self):
        h = XMPPNotifierHandler("alice@jabber.org")
        h._ready    = False
        h.xmlstream = MagicMock()
        assert h.send_message("Hola") is False

    def test_send_message_retorna_false_sin_xmlstream(self):
        h = XMPPNotifierHandler("alice@jabber.org")
        h._ready    = True
        h.xmlstream = None
        assert h.send_message("Hola") is False

    def test_connection_initialized_marca_ready(self):
        h = XMPPNotifierHandler("alice@jabber.org")
        h.xmlstream = MagicMock()
        h._ready    = False
        h.connectionInitialized()
        assert h._ready is True

    def test_connection_initialized_envia_presencia(self):
        h = XMPPNotifierHandler("alice@jabber.org")
        h.xmlstream = MagicMock()
        h.connectionInitialized()
        h.xmlstream.send.assert_called()

    def test_connection_lost_marca_no_listo(self, handler):
        handler.connectionLost("razón")
        assert handler._ready is False

    def test_send_message_usa_recipient_jid(self, handler):
        handler.send_message("Test")
        args = handler.xmlstream.send.call_args[0][0]
        assert args["to"] == "alice@jabber.org"

    def test_send_message_tipo_chat(self, handler):
        handler.send_message("Test")
        args = handler.xmlstream.send.call_args[0][0]
        assert args["type"] == "chat"


# ===========================================================================
# XMPPNotifierService
# ===========================================================================

class TestXMPPNotifierService:

    def test_check_mailbox_sin_correos_no_envia(self, service, user_dir):
        service._check_mailbox()
        service.handler.send_message.assert_not_called()

    def test_check_mailbox_con_correo_envia(self, service, user_dir):
        make_meta(user_dir, "msg001.eml")
        service._check_mailbox()
        service.handler.send_message.assert_called_once()

    def test_check_mailbox_mensaje_incluye_remitente(self, service, user_dir):
        make_meta(user_dir, "msg001.eml", sender="bob@ext.com")
        service._check_mailbox()
        args = service.handler.send_message.call_args[0][0]
        assert "bob@ext.com" in args

    def test_check_mailbox_no_repite_notificacion(self, service, user_dir):
        make_meta(user_dir, "msg001.eml")
        service._check_mailbox()
        service._check_mailbox()
        assert service.handler.send_message.call_count == 1

    def test_check_mailbox_notifica_correo_nuevo(self, service, user_dir):
        make_meta(user_dir, "msg001.eml")
        service._check_mailbox()
        make_meta(user_dir, "msg002.eml")
        service._check_mailbox()
        assert service.handler.send_message.call_count == 2

    def test_check_mailbox_rollback_si_send_falla(self, service, user_dir):
        service.handler.send_message.return_value = False
        make_meta(user_dir, "msg001.eml")
        service._check_mailbox()
        # Al fallar el envío, el correo debe poder notificarse de nuevo
        service.handler.send_message.return_value = True
        service._check_mailbox()
        assert service.handler.send_message.call_count == 2

    def test_service_usa_formatter(self, service, user_dir):
        make_meta(user_dir, "msg001.eml", sender="x@y.com", subject="Sub")
        service._check_mailbox()
        msg = service.handler.send_message.call_args[0][0]
        assert "x@y.com" in msg
        assert "Sub"     in msg


# ===========================================================================
# load_config
# ===========================================================================

class TestLoadConfig:

    def test_carga_config_valida(self, tmp_path):
        config = make_config(str(tmp_path))
        path   = tmp_path / "xmpp_config.json"
        path.write_text(json.dumps(config))
        result = load_config(str(path))
        assert result["jid"] == "bot@jabber.org"

    def test_error_si_no_existe(self):
        with pytest.raises(FileNotFoundError):
            load_config("no_existe.json")

    def test_error_si_falta_campo_jid(self, tmp_path):
        config = make_config(str(tmp_path))
        del config["jid"]
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(config))
        with pytest.raises(ValueError, match="jid"):
            load_config(str(path))

    def test_error_si_falta_campo_password(self, tmp_path):
        config = make_config(str(tmp_path))
        del config["password"]
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(config))
        with pytest.raises(ValueError):
            load_config(str(path))

    def test_error_si_falta_recipient_jid(self, tmp_path):
        config = make_config(str(tmp_path))
        del config["recipient_jid"]
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(config))
        with pytest.raises(ValueError):
            load_config(str(path))

    def test_error_si_falta_storage_path(self, tmp_path):
        config = make_config(str(tmp_path))
        del config["storage_path"]
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(config))
        with pytest.raises(ValueError):
            load_config(str(path))

    def test_error_si_falta_mail_user(self, tmp_path):
        config = make_config(str(tmp_path))
        del config["mail_user"]
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(config))
        with pytest.raises(ValueError):
            load_config(str(path))

    def test_campos_opcionales_no_requeridos(self, tmp_path):
        config = make_config(str(tmp_path))
        for campo in ("host", "port", "interval_seconds"):
            config.pop(campo, None)
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(config))
        result = load_config(str(path))
        assert result["jid"] == "bot@jabber.org"