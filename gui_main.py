import os
import sys
import json
import eel

sys.path.insert(0, os.path.dirname(__file__))

from smtp.smtpclient  import RecipientLoader, MessageLoader, SMTPSender, EmailBuilder
from user.pop3server  import UserAuth, POP3Mailbox

# ---------------------------------------------------------------------------
# Configuración Eel
# ---------------------------------------------------------------------------

eel.init("gui/dist")

STORAGE  = "mail_storage"
SMTP_CFG = "smtp/config.json"

# Usuario activo en sesión
_session = {
    "username":   None,
    "logged_in":  False,
    "smtp_host":  "localhost",
    "smtp_port":  2525,
    "smtp_tls":   False,
    "pop3_host":  "localhost",
    "pop3_port":  1100,
}


def _load_smtp_config() -> None:
    """Carga configuración SMTP desde config.json si existe y no está vacío."""
    if not os.path.exists(SMTP_CFG):
        return
    try:
        with open(SMTP_CFG) as f:
            content = f.read().strip()
        if not content:
            return
        cfg = json.loads(content)
        _session["smtp_host"] = cfg.get("host", "localhost")
        _session["smtp_port"] = cfg.get("port", 2525)
        _session["smtp_tls"]  = cfg.get("tls",  False)
    except json.JSONDecodeError:
        pass 


def _require_login() -> dict | None:
    """Retorna error estándar si no hay sesión activa."""
    if not _session["logged_in"]:
        return {"ok": False, "error": "No hay sesión activa"}
    return None


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------

@eel.expose
def login(username: str, password: str) -> dict:
    """
    Valida credenciales contra users.json.
    Retorna {"ok": True} o {"ok": False, "error": "..."}
    """
    auth = UserAuth(STORAGE)
    if not auth.validate(username, password):
        return {"ok": False, "error": "Usuario o contraseña incorrectos"}

    _session["username"]  = username
    _session["logged_in"] = True
    _load_smtp_config()
    return {"ok": True, "username": username}


@eel.expose
def logout() -> dict:
    _session["username"]  = None
    _session["logged_in"] = False
    return {"ok": True}


@eel.expose
def get_session() -> dict:
    return {
        "logged_in": _session["logged_in"],
        "username":  _session["username"],
    }


# ---------------------------------------------------------------------------
# Bandeja de entrada (POP3)
# ---------------------------------------------------------------------------

@eel.expose
def get_inbox() -> dict:
    """
    Lee los correos del usuario activo desde mail_storage/<usuario>/
    Retorna lista de metadatos sin el contenido crudo.
    """
    err = _require_login()
    if err:
        return err

    user_dir = os.path.join(STORAGE, _session["username"])
    if not os.path.isdir(user_dir):
        return {"ok": True, "emails": []}

    emails = []
    for filename in sorted(os.listdir(user_dir), reverse=True):
        if not filename.endswith(".json"):
            continue
        meta_path = os.path.join(user_dir, filename)
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            meta["id"] = filename.replace(".json", "")
            emails.append(meta)
        except (json.JSONDecodeError, OSError):
            continue

    return {"ok": True, "emails": emails}


@eel.expose
def get_email(email_id: str) -> dict:
    """
    Retorna el contenido completo de un correo por su ID.
    Marca el correo como leído.
    """
    err = _require_login()
    if err:
        return err

    user_dir  = os.path.join(STORAGE, _session["username"])
    eml_path  = os.path.join(user_dir, f"{email_id}.eml")
    meta_path = os.path.join(user_dir, f"{email_id}.json")

    if not os.path.exists(eml_path):
        return {"ok": False, "error": "Correo no encontrado"}

    with open(eml_path, "rb") as f:
        raw_bytes = f.read()

    # --- Parsear el mensaje MIME correctamente ---
    import email as email_lib
    from email import policy

    msg = email_lib.message_from_bytes(raw_bytes, policy=policy.default)

    # Extraer cuerpo legible
    body = _extract_body(msg)

    with open(meta_path) as f:
        meta = json.load(f)

    # Completar metadatos desde los headers del .eml si faltan
    if not meta.get("subject"):
        meta["subject"] = str(msg.get("Subject", "(sin asunto)"))
    if not meta.get("from"):
        meta["from"] = str(msg.get("From", ""))

    # Marcar como leído
    meta["read"] = True
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    return {
        "ok":   True,
        "meta": meta,
        "raw":  body,
    }


def _extract_body(msg) -> str:
    """
    Extrae el cuerpo legible de un mensaje MIME.
    Maneja texto plano, HTML, multipart y PGP/MIME.
    """
    import email as email_lib

    content_type = msg.get_content_type()
    subtype      = msg.get_content_subtype()

    # --- PGP/MIME cifrado (multipart/encrypted) ---
    if content_type == "multipart/encrypted":
        parts = msg.get_payload()
        if isinstance(parts, list) and len(parts) >= 2:
            # La segunda parte es el contenido cifrado
            encrypted_part = parts[1]
            payload = encrypted_part.get_payload(decode=False)
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="replace")
            return payload or ""
        return ""

    # --- PGP/MIME firmado (multipart/signed) ---
    if content_type == "multipart/signed":
        parts = msg.get_payload()
        if isinstance(parts, list) and len(parts) >= 1:
            return _extract_body(parts[0])
        return ""

    # --- Multipart genérico ---
    if msg.is_multipart():
        # Preferir text/plain sobre text/html
        plain = None
        html  = None
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not plain:
                payload = part.get_payload(decode=True)
                if payload:
                    plain = payload.decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace"
                    )
            elif ct == "text/html" and not html:
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace"
                    )
        return plain or html or ""

    # --- Texto plano o HTML simple ---
    payload = msg.get_payload(decode=True)
    if payload:
        return payload.decode(
            msg.get_content_charset() or "utf-8",
            errors="replace"
        )

    # --- Fallback: retornar payload como string ---
    raw = msg.get_payload(decode=False)
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw or ""


@eel.expose
def delete_email(email_id: str) -> dict:
    """Elimina un correo por su ID."""
    err = _require_login()
    if err:
        return err

    user_dir  = os.path.join(STORAGE, _session["username"])
    eml_path  = os.path.join(user_dir, f"{email_id}.eml")
    meta_path = os.path.join(user_dir, f"{email_id}.json")

    for path in (eml_path, meta_path):
        if os.path.exists(path):
            os.remove(path)

    return {"ok": True}


# ---------------------------------------------------------------------------
# Envío de correos (SMTP)
# ---------------------------------------------------------------------------
@eel.expose
def send_email(to: str, subject: str, body: str) -> dict:
    err = _require_login()
    if err:
        return err

    sender   = f"{_session['username']}@local.dev"
    template = {"subject": subject, "body": body, "attachment": None}

    try:
        builder = EmailBuilder(sender)
        data    = {"email": to, "nombre": to.split("@")[0]}
        builder.build(data, template)

        smtp = SMTPSender(
            host    = _session["smtp_host"],
            port    = _session["smtp_port"],
            use_tls = _session["smtp_tls"],
        )
        smtp.send_all(sender, [{"email": to, "nombre": to}], template)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@eel.expose
def send_bulk(
    csv_path:     str,
    message_path: str,
) -> dict:
    """Envío masivo desde la GUI usando CSV y plantilla."""
    err = _require_login()
    if err:
        return err

    try:
        sender     = f"{_session['username']}@local.dev"
        recipients = RecipientLoader(csv_path).load()
        template   = MessageLoader(message_path).load()

        smtp    = SMTPSender(
            host    = _session["smtp_host"],
            port    = _session["smtp_port"],
            use_tls = _session["smtp_tls"],
        )
        summary = smtp.send_all(sender, recipients, template)
        return {"ok": True, "summary": summary}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Configuración SMTP
# ---------------------------------------------------------------------------

@eel.expose
def save_smtp_config(
    host: str,
    port: int,
    tls:  bool,
) -> dict:
    config = {"host": host, "port": port, "tls": tls}
    with open(SMTP_CFG, "w") as f:
        json.dump(config, f, indent=2)
    _session["smtp_host"] = host
    _session["smtp_port"] = port
    _session["smtp_tls"]  = tls
    return {"ok": True}


@eel.expose
def get_smtp_config() -> dict:
    return {
        "ok":   True,
        "host": _session["smtp_host"],
        "port": _session["smtp_port"],
        "tls":  _session["smtp_tls"],
    }


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------

def main():
    _load_smtp_config()
    eel.start(
        "index.html",
        size      = (1100, 720),
        position  = (100, 80),
        port      = 8686,
        close_callback = lambda _, __: sys.exit(0),
    )


if __name__ == "__main__":
    main()