# xmpp/xmpp_notifier.py
"""
Notificador XMPP usando Twisted Words.
Monitorea mail_storage/<usuario>/ y notifica cuando llegan correos nuevos.
Uso: python xmpp/xmpp_notifier.py --config user/xmpp_config.json
"""

import os
import sys
import json
import argparse

from twisted.internet         import reactor, defer, task
from twisted.words.protocols  import jabber
from twisted.words.protocols.jabber         import client, jid
from twisted.words.protocols.jabber.xmlstream import XMPPHandler
from twisted.words.xish       import domish
from twisted.python           import log


# ---------------------------------------------------------------------------
# 1. Monitor de buzón
# ---------------------------------------------------------------------------

class MailboxMonitor:
    """
    Observa mail_storage/<usuario>/ en busca de correos con read=False.
    Mantiene un conjunto de archivos ya notificados para no repetir alertas.
    """

    def __init__(self, storage_path: str, username: str):
        self.user_dir  = os.path.join(storage_path, username)
        self._notified = set()

    def get_unread(self) -> list[dict]:
        """
        Retorna lista de metadatos de correos no leídos y aún no notificados.
        Cada elemento es el dict del .json correspondiente.
        """
        if not os.path.isdir(self.user_dir):
            return []

        unread = []
        for filename in sorted(os.listdir(self.user_dir)):
            if not filename.endswith(".json"):
                continue

            meta_path = os.path.join(self.user_dir, filename)
            if meta_path in self._notified:
                continue

            try:
                with open(meta_path, "r") as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            if not meta.get("read", True):
                unread.append(meta)
                self._notified.add(meta_path)

        return unread

    def count_unread(self) -> int:
        """Cuenta todos los correos con read=False (incluyendo ya notificados)."""
        if not os.path.isdir(self.user_dir):
            return 0

        count = 0
        for filename in os.listdir(self.user_dir):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.user_dir, filename)) as f:
                    meta = json.load(f)
                if not meta.get("read", True):
                    count += 1
            except (json.JSONDecodeError, OSError):
                continue
        return count


# ---------------------------------------------------------------------------
# 2. Formateador de mensajes XMPP
# ---------------------------------------------------------------------------

class NotificationFormatter:
    """
    Genera el texto de notificación a partir de los metadatos del correo.
    Separado del protocolo para facilitar pruebas y personalización.
    """

    def format_single(self, meta: dict) -> str:
        sender  = meta.get("from",      "desconocido")
        subject = meta.get("subject",   "(sin asunto)")
        ts      = meta.get("timestamp", "")
        return f"📧 Nuevo correo de {sender} — {subject} [{ts}]"

    def format_summary(self, count: int) -> str:
        if count == 1:
            return "Tenés 1 correo sin leer."
        return f"Tenés {count} correos sin leer."

    def format_notification(self, unread: list[dict], total_unread: int) -> str:
        lines = [self.format_summary(total_unread)]
        for meta in unread:
            lines.append(self.format_single(meta))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. Handler XMPP
# ---------------------------------------------------------------------------

class XMPPNotifierHandler(XMPPHandler):
    """
    XMPPHandler que:
    - Se autentica con el servidor XMPP.
    - Envía presencia (available) al conectarse.
    - Expone send_message() para enviar mensajes a un JID destino.
    """

    def __init__(self, recipient_jid: str):
        XMPPHandler.__init__(self)
        self.recipient_jid = recipient_jid
        self._ready        = False

    # --- Ciclo de vida ---

    def connectionInitialized(self):
        """Llamado por Twisted cuando la sesión XMPP está lista."""
        self._ready = True
        self._send_presence()
        log.msg(f"[XMPP] Conectado. Notificador listo para {self.recipient_jid}")

    def connectionLost(self, reason):
        self._ready = False
        log.msg(f"[XMPP] Conexión perdida: {reason}")

    # --- API pública ---

    def send_message(self, body: str) -> bool:
        """
        Envía un mensaje de tipo 'chat' al JID destinatario.
        Retorna True si se envió, False si la sesión no está lista.
        """
        if not self._ready or not self.xmlstream:
            log.msg("[XMPP] No se puede enviar: sesión no lista")
            return False

        msg = domish.Element(("jabber:client", "message"))
        msg["to"]   = self.recipient_jid
        msg["type"] = "chat"
        msg.addElement("body", content=body)

        self.xmlstream.send(msg)
        log.msg(f"[XMPP] Mensaje enviado a {self.recipient_jid}")
        return True

    # --- Helpers privados ---

    def _send_presence(self):
        presence = domish.Element(("jabber:client", "presence"))
        self.xmlstream.send(presence)


# ---------------------------------------------------------------------------
# 4. Servicio principal
# ---------------------------------------------------------------------------

class XMPPNotifierService:
    """
    Orquesta el monitor de buzón, el formateador y el handler XMPP.
    Polling configurable cada <interval> segundos.
    """

    def __init__(self, config: dict):
        self.config    = config
        self.monitor   = MailboxMonitor(
            config["storage_path"],
            config["mail_user"],
        )
        self.formatter = NotificationFormatter()
        self.handler   = XMPPNotifierHandler(config["recipient_jid"])
        self._loop: task.LoopingCall | None = None

    def start(self):
        """Conecta al servidor XMPP e inicia el polling."""
        cfg = self.config

        my_jid    = jid.JID(cfg["jid"])
        password  = cfg["password"]
        host      = cfg.get("host", my_jid.host)
        port      = cfg.get("port", 5222)
        interval  = cfg.get("interval_seconds", 30)

        # Construir la factoría de conexión XMPP
        factory = client.XMPPClientFactory(my_jid, password)
        factory.addBootstrap(
            jabber.xmlstream.STREAM_AUTHD_EVENT,
            self._on_authenticated,
        )
        factory.addBootstrap(
            jabber.xmlstream.INIT_FAILED_EVENT,
            self._on_auth_failed,
        )

        # Registrar el handler
        self.handler.setHandlerParent(
            jabber.xmlstream.XmlStreamServerFactory(factory)
            if False else factory   # simplificación: usar factory directamente
        )

        reactor.connectTCP(host, port, factory)
        log.msg(f"[XMPP] Conectando a {host}:{port} como {my_jid}")

        # Iniciar polling después de 2 s para dar tiempo a la autenticación
        reactor.callLater(2, self._start_polling, interval)

    def _on_authenticated(self, xmlstream):
        log.msg("[XMPP] Autenticación exitosa")
        self.handler.xmlstream = xmlstream
        self.handler._ready    = True
        self.handler._send_presence()

    def _on_auth_failed(self, reason):
        log.msg(f"[XMPP] Autenticación fallida: {reason}")

    def _start_polling(self, interval: int):
        self._loop = task.LoopingCall(self._check_mailbox)
        self._loop.start(interval, now=True)
        log.msg(f"[XMPP] Polling cada {interval}s")

    def _check_mailbox(self):
        unread = self.monitor.get_unread()
        if not unread:
            return

        total   = self.monitor.count_unread()
        message = self.formatter.format_notification(unread, total)
        sent    = self.handler.send_message(message)

        if not sent:
            # Si la sesión no está lista, devolver los correos al pool
            for meta in unread:
                path = os.path.join(
                    self.monitor.user_dir,
                    os.path.basename(meta.get("path", ""))
                        .replace(".eml", ".json")
                )
                self.monitor._notified.discard(path)


# ---------------------------------------------------------------------------
# 5. Carga de configuración
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """
    Carga y valida el archivo de configuración JSON.

    Campos requeridos:
        jid            : JID del notificador  (ej: bot@jabber.org)
        password       : Contraseña del JID
        recipient_jid  : JID del destinatario (ej: alice@jabber.org)
        storage_path   : Ruta a mail_storage/
        mail_user      : Usuario cuyo buzón se monitorea

    Campos opcionales:
        host             : Servidor XMPP (default: dominio del JID)
        port             : Puerto        (default: 5222)
        interval_seconds : Polling       (default: 30)
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuración no encontrada: {config_path}")

    with open(config_path, "r") as f:
        config = json.load(f)

    required = {"jid", "password", "recipient_jid", "storage_path", "mail_user"}
    missing  = required - set(config.keys())
    if missing:
        raise ValueError(f"Faltan campos en la configuración: {missing}")

    return config


# ---------------------------------------------------------------------------
# 6. Punto de entrada
# ---------------------------------------------------------------------------

def _parse_args():
    parser = argparse.ArgumentParser(description="Notificador XMPP para nuevos correos")
    parser.add_argument(
        "--config",
        default="user/xmpp_config.json",
        help="Ruta al archivo de configuración JSON"
    )
    return parser.parse_args()


def main():
    log.startLogging(sys.stdout)
    args    = _parse_args()
    config  = load_config(args.config)
    service = XMPPNotifierService(config)
    service.start()
    reactor.run()


if __name__ == "__main__":
    main()