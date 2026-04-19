import os
import sys
import csv
import re
import argparse
import smtplib

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger(__name__)

## @package smtp.smtpclient
# Cliente SMTP masivo con soporte para plantillas personalizadas.
# Uso: python smtp/smtpclient.py -host <mail-server> -c <csv-file> -m <message-file>
class RecipientLoader:
    """
    @class RecipientLoader
        Lee el CSV de destinatarios y normaliza las claves a minúsculas.
    """

    REQUIRED_COLUMNS = {"email", "nombre"}

    def __init__(self, csv_path: str):
        self.csv_path = csv_path

    def load(self) -> list[dict]:
        """
        @method load
            @return list[dict]: Lista de destinatarios.

            Lee el CSV y retorna una lista de diccionarios con claves normalizadas.
        """
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"CSV no encontrado: {self.csv_path}")

        recipients = []
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            self._validate_columns(reader.fieldnames or [])

            for row in reader:
                normalized = {k.lower().strip(): v.strip() for k, v in row.items()}
                if not normalized.get("email"):
                    continue
                recipients.append(normalized)

        return recipients

    def _validate_columns(self, fieldnames: list[str]) -> None:
        """
        @method _validate_columns
            @param fieldnames: Lista de nombres de columnas del CSV.
            @return: None.

            Verifica que el CSV tenga al menos las columnas requeridas (email, nombre).
            Las claves se normalizan a minúsculas para evitar problemas de mayúsculas.
        """
        present = {c.lower() for c in fieldnames}
        if not self.REQUIRED_COLUMNS.issubset(present):
            raise ValueError(
                f"El CSV debe tener al menos las columnas: {self.REQUIRED_COLUMNS}"
            )


class TemplateRenderer:
    """
    @class TemplateRenderer
        Sustituye variables con sintaxis {{variable}} usando las columnas del CSV.
        Variables no encontradas se conservan sin cambio para facilitar el debug.
    """

    PATTERN = re.compile(r"\{\{(.+?)\}\}")

    def render(self, template: str, variables: dict) -> str:
        """
        @method render
            @param template: cadena con variables en formato {{variable}}.
            @param variables: diccionario con valores para sustituir.
            @return: str: plantilla con variables sustituidas.

            Reemplaza las variables en la plantilla usando el diccionario de datos.
            Si una variable no se encuentra en el diccionario, se deja sin cambio.
        """
        def replacer(match):
            """
            @method replacer
                @param match: objeto de coincidencia de regex para {{variable}}
                @return: str: valor sustituido o el texto original si no se encuentra la variable.
            """
            key = match.group(1).strip().lower()
            return variables.get(key, match.group(0))

        return self.PATTERN.sub(replacer, template)


class MessageLoader:
    """
    @class MessageLoader
    Lee el archivo de plantilla del mensaje.

    Formato esperado:
        Subject: {{nombre}}, aquí está tu información
        Attachment: ruta/al/adjunto.pdf   ← opcional
        [línea en blanco]
        Cuerpo del mensaje con {{variables}}.
    """

    def __init__(self, message_path: str):
        self.message_path = message_path

    def load(self) -> dict:
        """
        @method load
            @return dict: diccionario con claves 'subject', 'body' y 'attachment'.

            Lee el archivo de plantilla y lo parsea en un diccionario.
             - 'subject': texto del asunto (puede contener variables).
             - 'body': texto del cuerpo (puede contener variables).
             - 'attachment': ruta al adjunto (opcional, puede contener variables).
        """
        if not os.path.exists(self.message_path):
            raise FileNotFoundError(
                f"Archivo de mensaje no encontrado: {self.message_path}"
            )

        with open(self.message_path, "r", encoding="utf-8") as f:
            content = f.read()

        return self._parse(content)

    def _parse(self, content: str) -> dict:
        """
        @method _parse
            @param content: contenido del archivo de plantilla.
            @return dict: diccionario con claves 'subject', 'body' y 'attachment'.

            Parsea el contenido del archivo de plantilla en un diccionario.
        """

        lines      = content.splitlines()
        subject    = ""
        attachment = None
        body_start = 0

        for i, line in enumerate(lines):
            if line.strip() == "":
                body_start = i + 1
                break
            lower = line.lower()
            if lower.startswith("subject:"):
                subject = line[len("subject:"):].strip()
            elif lower.startswith("attachment:"):
                attachment = line[len("attachment:"):].strip()

        body = "\n".join(lines[body_start:])

        return {
            "subject":    subject or "(sin asunto)",
            "body":       body,
            "attachment": attachment,
        }


class EmailBuilder:
    """
    @class EmailBuilder
        Construye objetos MIMEMultipart listos para enviar, aplicando el motor de plantillas al asunto, cuerpo y ruta del adjunto.
    """

    def __init__(self, sender: str):
        self.sender   = sender
        self.renderer = TemplateRenderer()

    def build(self, recipient_data: dict, template: dict) -> MIMEMultipart:
        """
        @method build
            @param recipient_data: diccionario con datos del destinatario (ej: email, nombre).
            @param template: diccionario con claves 'subject', 'body' y 'attachment'.
            @return MIMEMultipart: mensaje listo para enviar.

            Construye el mensaje aplicando el motor de plantillas al asunto, cuerpo y ruta del adjunto.
        """
        subject = self.renderer.render(template["subject"], recipient_data)
        body    = self.renderer.render(template["body"],    recipient_data)

        msg = MIMEMultipart()
        msg["From"]    = self.sender
        msg["To"]      = recipient_data["email"]
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain", "utf-8"))
        self._attach_file(msg, template.get("attachment"), recipient_data)

        return msg

    def _attach_file(
        self,
        msg: MIMEMultipart,
        attachment_path: str | None,
        variables: dict,
    ) -> None:
        """
        @method _attach_file
            @param msg: mensaje MIMEMultipart al que se agregará el adjunto.
            @param attachment_path: ruta al archivo a adjuntar (puede contener variables).
            @param variables: diccionario con variables para reemplazar en la ruta.
        """

        if not attachment_path:
            return

        path = self.renderer.render(attachment_path, variables)

        if not os.path.exists(path):
            logger.warning(f"  [!] Adjunto no encontrado, se omite: {path}")
            return

        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())

        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(path)}"'
        )
        msg.attach(part)

class SMTPSender:
    """
    @class SMTPSender
        Abre una conexión SMTP y envía el correo a cada destinatario.
        Retorna un resumen con listas 'ok' y 'failed'.
    """

    def __init__(
        self,
        host: str,
        port: int,
        use_tls:  bool = False,
        username: str  = None,
        password: str  = None,
    ):
        self.host     = host
        self.port     = port
        self.use_tls  = use_tls
        self.username = username
        self.password = password

    def send_all(
        self,
        sender:     str,
        recipients: list[dict],
        template:   dict,
    ) -> dict:
        """
        @method send_all
            @param sender: dirección del remitente.
            @param recipients: lista de diccionarios con datos de destinatarios.
            @param template: diccionario con claves 'subject', 'body' y 'attachment'.
            @return dict: resumen con listas 'ok' y 'failed'.

            Envía el correo a cada destinatario usando la plantilla y retorna un resumen.
        """
        summary    = {"ok": [], "failed": []}
        builder    = EmailBuilder(sender)
        connection = self._connect()

        for data in recipients:
            email = data["email"]
            try:
                msg = builder.build(data, template)
                connection.sendmail(sender, email, msg.as_string())
                logger.info(f"  [OK]  → {email}")
                summary["ok"].append(email)
            except Exception as e:
                logger.error(f"  [FAIL] → {email} : {e}")
                summary["failed"].append({"email": email, "error": str(e)})

        connection.quit()
        return summary

    def _connect(self) -> smtplib.SMTP:
        """
        @method _connect
            @return smtplib.SMTP: conexión SMTP establecida.
        """
        try:
            conn = smtplib.SMTP(self.host, self.port, timeout=10)
            conn.ehlo()

            if self.use_tls:
                if "starttls" not in conn.esmtp_features:
                    conn.quit()
                    logger.error("[ERROR] El servidor no soporta STARTTLS.")
                    logger.info("Corré el servidor con --cert y --key, o quitá --tls.")
                    sys.exit(1)
                conn.starttls()
                conn.ehlo()

            if self.username and self.password:
                conn.login(self.username, self.password)

            return conn

        except SystemExit:
            raise
        except Exception as e:
            logger.error(f"[ERROR] No se pudo conectar a {self.host}:{self.port} — {e}")
            sys.exit(1)

# ====================================================================
def _parse_args():
    """
    @function _parse_args
        Define y parsea los argumentos de línea de comandos para el cliente SMTP.
        -host: servidor SMTP (ej: localhost o smtp.gmail.com)
        -c, --csv: archivo CSV con destinatarios (email, nombre, ...)
        -m, --message: archivo de plantilla del mensaje
        -p, --port: puerto del servidor SMTP (default: 2525)
        -s, --sender: dirección del remitente (default: noreply@local.dev)
        --tls: usar STARTTLS
        --user: usuario para autenticación SMTP
        --password: contraseña para autenticación SMTP

        @return argparse.Namespace: objeto con los argumentos parseados.
    """
    parser = argparse.ArgumentParser(description="Cliente SMTP masivo con plantillas")
    parser.add_argument("-host", "--host",     required=True,
                        dest="host",
                        help="Servidor de correo. Ej: localhost o smtp.gmail.com")
    parser.add_argument("-c", "--csv",      required=True,
                        help="Archivo CSV con destinatarios (email, nombre, ...)")
    parser.add_argument("-m", "--message",  required=True,
                        help="Archivo de plantilla del mensaje")
    parser.add_argument("-p", "--port",     type=int, default=2525,
                        help="Puerto del servidor SMTP (default: 2525)")
    parser.add_argument("-s", "--sender",   default="noreply@local.dev",
                        help="Dirección del remitente")
    parser.add_argument("--tls",            action="store_true",
                        help="Usar STARTTLS")
    parser.add_argument("--user",           default=None,
                        help="Usuario para autenticación SMTP")
    parser.add_argument("--password",       default=None,
                        help="Contraseña para autenticación SMTP")
    return parser.parse_args()


def main():
    """
    @function main
        Función principal que orquesta la carga de destinatarios, lectura de plantilla, construcción y envío de correos.
        - Carga destinatarios desde el CSV usando RecipientLoader.
        - Lee la plantilla del mensaje usando MessageLoader.
        - Imprime un resumen de la configuración y cantidad de destinatarios.
        - Crea una instancia de SMTPSender con la configuración SMTP.
        - Envía el correo a todos los destinatarios y obtiene un resumen de resultados.
        - Imprime un resumen final con la cantidad de envíos exitosos y fallidos con sus respectivos detalles.
    """
    args     = _parse_args()
    loader   = RecipientLoader(args.csv)
    recipients = loader.load()

    msg_loader = MessageLoader(args.message)
    template   = msg_loader.load()

    logger.info(f"\n[SMTP Client] Servidor : {args.host}:{args.port}")
    logger.info(f"[SMTP Client] Remitente: {args.sender}")
    logger.info(f"[SMTP Client] Total    : {len(recipients)} destinatarios\n")

    smtp_sender = SMTPSender(
        host     = args.host,
        port     = args.port,
        use_tls  = args.tls,
        username = args.user,
        password = args.password,
    )

    summary = smtp_sender.send_all(args.sender, recipients, template)

    logger.info(f"\n[Resumen] OK: {len(summary['ok'])} | Fallidos: {len(summary['failed'])}")
    if summary["failed"]:
        logger.warning("[Fallidos]")
        for f in summary["failed"]:
            logger.error(f"  {f['email']} — {f['error']}")


if __name__ == "__main__":
    main()