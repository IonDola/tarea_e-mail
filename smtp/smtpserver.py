import sys
import os
import json
import argparse
from datetime import datetime

from twisted.internet import reactor, ssl, defer
from twisted.mail import smtp
from twisted.python import log
from zope.interface import implementer

## @package smtp.smtpserver
#  Servidor SMTP usando Twisted.
#  Uso: python smtpserver.py -d <dominios> -s <mail-storage> -p <puerto>

class MaildirStorage:
    """
    @class MaildirStorage
        Guarda cada correo en mail_storage/<usuario>/
        como archivo .eml y un .json con metadatos.
    """

    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        os.makedirs(storage_path, exist_ok=True)

    def _user_dir(self, user: str) -> str:
        """
        @method _user_dir
            @param user: nombre de usuario (parte local del email).
            @return: ruta al directorio del usuario.

            Devuelve (y crea si no existe) el directorio del usuario.
            Ej: 'alice' → 'mail_storage/alice/'.
        """
        path = os.path.join(self.storage_path, user)
        os.makedirs(path, exist_ok=True)
        return path

    def save(self, recipient: str, sender: str, raw_message: bytes) -> str:
        """
        @method save
            @param recipient: dirección de destino (ej: 'sarah@ejemplo.com').
            @param sender: dirección del remitente (ej: 'ion@correo.com').
            @param raw_message: contenido del correo en bytes (incluye headers).
            @return: ruta al archivo .eml guardado.

            Persiste el mensaje.
            Retorna la ruta del archivo .eml guardado.
        """
        user = recipient.split("@")[0] 
        user_dir = self._user_dir(user)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        eml_path  = os.path.join(user_dir, f"{timestamp}.eml")
        meta_path = os.path.join(user_dir, f"{timestamp}.json")

        # Correo crudo
        with open(eml_path, "wb") as f:
            f.write(raw_message)

        # Metadatos (usados por el servidor POP3 y el notificador XMPP)
        meta = {
            "from":      sender,
            "to":        recipient,
            "timestamp": timestamp,
            "read":      False,
            "path":      eml_path,
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        log.msg(f"[storage] Guardado: {eml_path}")
        return eml_path


# ---------------------------------------------------------------------------
# 2. Receptor de mensajes (por destinatario)
# ---------------------------------------------------------------------------
@implementer(smtp.IMessage)
class SMTPMessage:
    """
    @class SMTPMessage
        Recibe las líneas del cuerpo del correo y lo persiste al terminar.
        Twisted llama a lineReceived() por cada línea y a eomReceived() al final.
    """

    def __init__(self, storage: MaildirStorage, recipient: str, sender: str):
        self.storage   = storage
        self.recipient = recipient
        self.sender    = sender
        self.lines: list[bytes] = []

    # --- IMessage ---

    def lineReceived(self, line: bytes) -> None:
        """
        @method lineReceived
            @param line: línea del mensaje (bytes, sin CRLF).
            Acumula cada línea del mensaje. line es un bytes sin el CRLF final.
        """
        self.lines.append(line)

    def eomReceived(self) -> defer.Deferred:
        """
        @method eomReceived
            @return: Deferred que se resuelve cuando el mensaje se ha procesado.
            Se llama al finalizar el mensaje (después de la última línea).
            Persiste el mensaje usando MaildirStorage.
        """
        raw = b"\r\n".join(self.lines)
        self.storage.save(self.recipient, self.sender, raw)
        self.lines = []
        return defer.succeed(None)

    def connectionLost(self) -> None:
        """
        @method connectionLost
            Se llama si la conexión cae antes de finalizar el mensaje.
             Descartamos lo acumulado.
        """
        self.lines = []


# ---------------------------------------------------------------------------
# 3. Entrega (delivery): valida dominios y crea SMTPMessage por destinatario
# ---------------------------------------------------------------------------

@implementer(smtp.IMessageDelivery)
class SMTPDelivery:
    """
    @class SMTPDelivery
        Valida remitente y destinatarios, y crea un SMTPMessage para cada destinatario.
        Twisted llama a validateFrom / validateTo antes de aceptar un mensaje.
        Aquí aplicamos la política de dominios aceptados.
    """

    def __init__(self, storage: MaildirStorage, accepted_domains: list[str]):
        self.storage          = storage
        self.accepted_domains = [d.lower() for d in accepted_domains]
        self._sender          = ""

    def validateFrom(self, helo, origin):
        """
        @method validateFrom
            @param helo: tupla (hostname, port) del cliente.
            @param origin: dirección del remitente (Address).
            @return: dirección del remitente (Address).
            Acepta cualquier remitente (política abierta para la tarea).
        """
        self._sender = str(origin)
        return origin

    def validateTo(self, user):
        """
        @method validateTo
            @param user: objeto smtp.User con atributo .dest (Address).
            @return: callable que devuelve un IMessage.
            Rechaza correos cuyo dominio no esté en la lista configurada.
        """
        address = str(user.dest)
        domain  = address.split("@")[-1].lower() if "@" in address else ""

        if domain not in self.accepted_domains:
            raise smtp.SMTPBadRcpt(user)

        # Retorna un callable que Twisted invocará para obtener el IMessage
        return lambda: SMTPMessage(self.storage, address, self._sender)

    def receivedHeader(self, helo, origin, recipients):
        """
        @method receivedHeader
            @param helo: tupla (hostname, port) del cliente.
            @param origin: dirección del remitente (Address).
            @param recipients: lista de direcciones de destinatarios (Address).
            @return: cabecera 'Received:' que el servidor añade al mensaje.
        """
        timestamp = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
        helo_name = helo[0].decode() if isinstance(helo[0], bytes) else helo[0]
        return (
            f"Received: from {helo_name} by tarea-smtp; {timestamp}"
        ).encode()


# ---------------------------------------------------------------------------
# 4. Fábrica del servidor SMTP
# ---------------------------------------------------------------------------

class SMTPFactory(smtp.SMTPFactory):
    """
    @class SMTPFactory
        Fábrica que Twisted usa para crear una instancia de protocolo por conexión.
        Sobreescribimos buildProtocol para inyectar nuestro delivery.
    """

    protocol = smtp.ESMTP          # ESMTP habilita EHLO y extensiones (STARTTLS)

    def __init__(self, storage: MaildirStorage, accepted_domains: list[str]):
        self.storage          = storage
        self.accepted_domains = accepted_domains
        # Contexto TLS (se asigna desde main si los certs están disponibles)
        self.ssl_context = None

    def buildProtocol(self, addr):
        """
        @method buildProtocol
            @param addr: dirección del cliente (tupla (host, port)).
            @return: instancia de protocolo para manejar la conexión.
            Crea una instancia del protocolo (ESMTP), asigna el delivery y el contexto TLS.
        """
        p = self.protocol()
        p.factory  = self
        p.delivery = SMTPDelivery(self.storage, self.accepted_domains)

        # Inyectar contexto TLS para que ESMTP ofrezca STARTTLS
        if self.ssl_context:
            p.startTLS = self.ssl_context

        return p


# ---------------------------------------------------------------------------
# 5. Punto de entrada
# ---------------------------------------------------------------------------

def _parse_args():
    """
    @function _parse_args
        Analiza los argumentos de línea de comandos usando argparse.
        -d / --domains: lista de dominios aceptados (requerido).
        -s / --storage: directorio raíz para guardar correos (requerido).
        -p / --port: puerto de escucha (default: 2525).
        --cert: ruta al certificado SSL (default: certs/server.crt).
        --key: ruta a la llave privada SSL (default: certs/server.key).

        Retorna un objeto con los argumentos analizados.
    """
    parser = argparse.ArgumentParser(description="Servidor SMTP con Twisted")
    parser.add_argument("-d", "--domains",  required=True,
                        help="Dominios aceptados separados por coma. Ej: local.dev,example.com")
    parser.add_argument("-s", "--storage",  required=True,
                        help="Directorio raíz de almacenamiento de correos")
    parser.add_argument("-p", "--port",     type=int, default=2525,
                        help="Puerto de escucha (default: 2525)")
    parser.add_argument("--cert",           default="certs/server.crt",
                        help="Ruta al certificado SSL")
    parser.add_argument("--key",            default="certs/server.key",
                        help="Ruta a la llave privada SSL")
    return parser.parse_args()


def main():
    """
    @function main
        Punto de entrada del servidor SMTP.
        - Configura logging.
        - Analiza argumentos.
        - Crea MaildirStorage y SMTPFactory.
        - Configura TLS si los certificados están disponibles.
        - Inicia el reactor de Twisted para escuchar conexiones SMTP.

        El servidor se ejecuta indefinidamente hasta que se detenga manualmente (Ctrl+C).
    """
    log.startLogging(sys.stdout)

    args    = _parse_args()
    domains = [d.strip() for d in args.domains.split(",")]
    storage = MaildirStorage(args.storage)
    factory = SMTPFactory(storage, domains)

    # --- TLS opcional (STARTTLS sobre el mismo puerto) ---
    if os.path.exists(args.cert) and os.path.exists(args.key):
        try:
            ctx = ssl.DefaultOpenSSLContextFactory(args.key, args.cert)
            factory.ssl_context = ctx
            log.msg("[TLS] STARTTLS habilitado")
        except Exception as e:
            log.msg(f"[TLS] No se pudo cargar certs: {e} — arrancando sin TLS")
    else:
        log.msg("[TLS] Certificados no encontrados — arrancando sin TLS")

    reactor.listenTCP(args.port, factory)
    log.msg(f"[SMTP] Escuchando en puerto {args.port} | Dominios: {domains}")
    reactor.run()


if __name__ == "__main__":
    main()