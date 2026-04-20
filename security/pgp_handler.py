# security/pgp_handler.py
"""
Manejo de cifrado y firmado PGP/MIME usando python-gnupg.
Permite cifrar, descifrar, firmar y verificar correos electrónicos.
"""

import os
import sys
import gnupg
import tempfile

from email.mime.multipart  import MIMEMultipart
from email.mime.base       import MIMEBase
from email.mime.text       import MIMEText
from email.mime.application import MIMEApplication
from email                 import encoders


# ---------------------------------------------------------------------------
# 1. Gestión del keyring
# ---------------------------------------------------------------------------

class PGPKeyManager:
    """
    Gestiona el keyring GPG del sistema.
    Permite importar, exportar, generar y listar llaves.
    """

    def __init__(self, keys_dir: str = "security/keys"):
        self.keys_dir = keys_dir
        os.makedirs(keys_dir, exist_ok=True)
        self.gpg = gnupg.GPG(gnupghome=keys_dir)
        self.gpg.encoding = "utf-8"

    # --- Generación ---

    def generate_key(
        self,
        name:       str,
        email:      str,
        passphrase: str = "",
        key_type:   str = "RSA",
        key_length: int = 2048,
    ) -> str:
        """
        Genera un par de llaves PGP.
        Retorna el fingerprint de la llave generada.
        """
        input_data = self.gpg.gen_key_input(
            key_type       = key_type,
            key_length     = key_length,
            name_real      = name,
            name_email     = email,
            passphrase     = passphrase,
            expire_date    = "1y",
        )
        result = self.gpg.gen_key(input_data)
        if not result.fingerprint:
            raise RuntimeError(f"Error generando llave: {result.stderr}")
        return result.fingerprint

    # --- Importar / Exportar ---

    def import_key(self, key_data: str) -> list[str]:
        """
        Importa una llave pública o privada en formato ASCII armored.
        Retorna lista de fingerprints importados.
        """
        result = self.gpg.import_keys(key_data)
        if not result.fingerprints:
            raise ValueError("No se pudo importar la llave — formato inválido")
        return result.fingerprints

    def import_key_from_file(self, key_path: str) -> list[str]:
        """Importa una llave desde un archivo .asc."""
        if not os.path.exists(key_path):
            raise FileNotFoundError(f"Archivo de llave no encontrado: {key_path}")
        with open(key_path, "r") as f:
            return self.import_key(f.read())

    def export_public_key(self, email: str) -> str:
        """Exporta la llave pública de un email en formato ASCII armored."""
        key = self.gpg.export_keys(email)
        if not key:
            raise ValueError(f"No se encontró llave pública para: {email}")
        return key

    def export_private_key(self, email: str, passphrase: str = "") -> str:
        """Exporta la llave privada de un email en formato ASCII armored."""
        key = self.gpg.export_keys(email, secret=True, passphrase=passphrase)
        if not key:
            raise ValueError(f"No se encontró llave privada para: {email}")
        return key

    def save_public_key(self, email: str) -> str:
        """Exporta y guarda la llave pública en security/keys/<email>.asc"""
        key  = self.export_public_key(email)
        path = os.path.join(self.keys_dir, f"{email}.asc")
        with open(path, "w") as f:
            f.write(key)
        return path

    # --- Consulta ---

    def list_keys(self, secret: bool = False) -> list[dict]:
        """Lista todas las llaves en el keyring."""
        return self.gpg.list_keys(secret)

    def key_exists(self, email: str) -> bool:
        """Verifica si existe una llave pública para el email dado."""
        keys = self.gpg.list_keys()
        for key in keys:
            for uid in key.get("uids", []):
                if email.lower() in uid.lower():
                    return True
        return False

    def delete_key(self, fingerprint: str, secret: bool = False) -> None:
        """Elimina una llave del keyring por fingerprint."""
        if secret:
            self.gpg.delete_keys(fingerprint, secret=True)
        self.gpg.delete_keys(fingerprint)


# ---------------------------------------------------------------------------
# 2. Cifrado y descifrado
# ---------------------------------------------------------------------------

class PGPEncryptor:
    """
    Cifra y descifra texto o archivos usando llaves del keyring.
    """

    def __init__(self, key_manager: PGPKeyManager):
        self.gpg = key_manager.gpg

    def encrypt(
        self,
        data:        str | bytes,
        recipients:  list[str],
        passphrase:  str = "",
        sign:        str = None,
    ) -> str:
        """
        Cifra datos para uno o más destinatarios.

        Args:
            data       : Texto o bytes a cifrar
            recipients : Lista de emails o fingerprints destinatarios
            passphrase : Passphrase de la llave de firma (si sign != None)
            sign       : Email o fingerprint de la llave con que firmar

        Retorna el texto cifrado en formato ASCII armored.
        """
        result = self.gpg.encrypt(
            data,
            recipients,
            sign       = sign,
            passphrase = passphrase,
            always_trust = True,
        )
        if not result.ok:
            raise RuntimeError(f"Error cifrando: {result.stderr}")
        return str(result)

    def decrypt(
        self,
        encrypted_data: str,
        passphrase:     str = "",
    ) -> str:
        """
        Descifra datos cifrados con PGP.
        Retorna el texto plano descifrado.
        """
        result = self.gpg.decrypt(encrypted_data, passphrase=passphrase)
        if not result.ok:
            raise RuntimeError(f"Error descifrando: {result.stderr}")
        return str(result)

    def encrypt_file(
        self,
        file_path:   str,
        recipients:  list[str],
        output_path: str = None,
    ) -> str:
        """
        Cifra un archivo y lo guarda con extensión .gpg.
        Retorna la ruta del archivo cifrado.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

        output_path = output_path or file_path + ".gpg"

        with open(file_path, "rb") as f:
            result = self.gpg.encrypt_file(
                f,
                recipients,
                output       = output_path,
                always_trust = True,
            )

        if not result.ok:
            raise RuntimeError(f"Error cifrando archivo: {result.stderr}")
        return output_path

    def decrypt_file(
        self,
        encrypted_path: str,
        passphrase:     str = "",
        output_path:    str = None,
    ) -> str:
        """
        Descifra un archivo .gpg.
        Retorna la ruta del archivo descifrado.
        """
        if not os.path.exists(encrypted_path):
            raise FileNotFoundError(f"Archivo cifrado no encontrado: {encrypted_path}")

        output_path = output_path or encrypted_path.replace(".gpg", ".dec")

        with open(encrypted_path, "rb") as f:
            result = self.gpg.decrypt_file(
                f,
                passphrase = passphrase,
                output     = output_path,
            )

        if not result.ok:
            raise RuntimeError(f"Error descifrando archivo: {result.stderr}")
        return output_path


# ---------------------------------------------------------------------------
# 3. Firmado y verificación
# ---------------------------------------------------------------------------

class PGPSigner:
    """
    Firma y verifica mensajes usando llaves PGP.
    """

    def __init__(self, key_manager: PGPKeyManager):
        self.gpg = key_manager.gpg

    def sign(
        self,
        data:       str | bytes,
        signer:     str,
        passphrase: str = "",
        detached:   bool = True,
    ) -> str:
        """
        Firma datos con la llave privada del signer.

        Args:
            data       : Texto o bytes a firmar
            signer     : Email o fingerprint de la llave firmante
            passphrase : Passphrase de la llave privada
            detached   : True → firma separada, False → firma inline

        Retorna la firma en formato ASCII armored.
        """
        result = self.gpg.sign(
            data,
            keyid      = signer,
            passphrase = passphrase,
            detach     = detached,
        )
        if not result.fingerprint and not str(result):
            raise RuntimeError(f"Error firmando: {result.stderr}")
        return str(result)

    def verify(self, data: str, signature: str = None) -> dict:
        """
        Verifica la firma de un mensaje.

        Args:
            data      : Datos firmados (o datos originales si firma es detached)
            signature : Firma detached en ASCII armored (None si es inline)

        Retorna dict con:
            valid       : bool
            fingerprint : str
            username    : str
            timestamp   : str
        """
        if signature:
            # Firma detached: escribir firma a archivo temporal
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".asc", delete=False
            ) as sig_file:
                sig_file.write(signature)
                sig_path = sig_file.name

            try:
                result = self.gpg.verify_data(sig_path, data.encode())
            finally:
                os.unlink(sig_path)
        else:
            result = self.gpg.verify(data)

        return {
            "valid":       result.valid,
            "fingerprint": result.fingerprint or "",
            "username":    result.username    or "",
            "timestamp":   result.timestamp   or "",
            "status":      result.status      or "",
        }


# ---------------------------------------------------------------------------
# 4. Constructor de correos PGP/MIME
# ---------------------------------------------------------------------------

class PGPMIMEBuilder:
    """
    Construye correos cifrados y/o firmados según el estándar PGP/MIME
    (RFC 3156).

    Modos soportados:
        - sign_only    : multipart/signed
        - encrypt_only : multipart/encrypted
        - sign_encrypt : cifrado + firmado
    """

    def __init__(self, key_manager: PGPKeyManager):
        self.encryptor = PGPEncryptor(key_manager)
        self.signer    = PGPSigner(key_manager)

    def build_signed(
        self,
        sender:     str,
        recipients: list[str],
        subject:    str,
        body:       str,
        passphrase: str = "",
    ) -> MIMEMultipart:
        """
        Construye un correo multipart/signed (RFC 3156 §5).
        El cuerpo es legible sin PGP, la firma va como adjunto.
        """
        # Parte del cuerpo
        body_part = MIMEText(body, "plain", "utf-8")

        # Firma detached del cuerpo
        signature = self.signer.sign(
            body_part.as_string(),
            signer     = sender,
            passphrase = passphrase,
            detached   = True,
        )

        # Parte de la firma
        sig_part = MIMEBase("application", "pgp-signature")
        sig_part.set_payload(signature)
        sig_part.add_header("Content-Disposition", 'attachment; filename="signature.asc"')

        # Ensamblar multipart/signed
        msg = MIMEMultipart(
            "signed",
            micalg   = "pgp-sha256",
            protocol = "application/pgp-signature",
        )
        msg["From"]    = sender
        msg["To"]      = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(body_part)
        msg.attach(sig_part)

        return msg

    def build_encrypted(
        self,
        sender:     str,
        recipients: list[str],
        subject:    str,
        body:       str,
        passphrase: str = "",
        sign:       bool = False,
    ) -> MIMEMultipart:
        """
        Construye un correo multipart/encrypted (RFC 3156 §4).
        El cuerpo queda completamente cifrado.
        """
        # Parte del cuerpo a cifrar
        body_part  = MIMEText(body, "plain", "utf-8")
        plain_text = body_part.as_string()

        # Cifrar (y opcionalmente firmar)
        encrypted = self.encryptor.encrypt(
            plain_text,
            recipients = recipients,
            passphrase = passphrase if sign else "",
            sign       = sender if sign else None,
        )

        # Parte de control PGP (obligatoria según RFC 3156)
        control_part = MIMEBase("application", "pgp-encrypted")
        control_part.set_payload("Version: 1\n")

        # Parte con el contenido cifrado
        encrypted_part = MIMEBase("application", "octet-stream")
        encrypted_part.set_payload(encrypted)
        encrypted_part.add_header(
            "Content-Disposition", 'inline; filename="encrypted.asc"'
        )

        # Ensamblar multipart/encrypted
        msg = MIMEMultipart(
            "encrypted",
            protocol = "application/pgp-encrypted",
        )
        msg["From"]    = sender
        msg["To"]      = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(control_part)
        msg.attach(encrypted_part)

        return msg

    def decrypt_mime(
        self,
        mime_message: MIMEMultipart,
        passphrase:   str = "",
    ) -> str:
        """
        Descifra un correo multipart/encrypted.
        Retorna el texto plano del cuerpo.
        """
        if mime_message.get_content_subtype() != "encrypted":
            raise ValueError("El mensaje no es multipart/encrypted")

        parts = mime_message.get_payload()
        if len(parts) < 2:
            raise ValueError("Estructura PGP/MIME inválida")

        # La segunda parte es el contenido cifrado
        encrypted_data = parts[1].get_payload()
        return self.encryptor.decrypt(encrypted_data, passphrase)

    def verify_mime(self, mime_message: MIMEMultipart) -> dict:
        """
        Verifica la firma de un correo multipart/signed.
        Retorna el resultado de la verificación.
        """
        if mime_message.get_content_subtype() != "signed":
            raise ValueError("El mensaje no es multipart/signed")

        parts = mime_message.get_payload()
        if len(parts) < 2:
            raise ValueError("Estructura PGP/MIME inválida")

        body_text = parts[0].as_string()
        signature = parts[1].get_payload()

        return self.signer.verify(body_text, signature)


# ---------------------------------------------------------------------------
# 5. Fachada principal
# ---------------------------------------------------------------------------

class PGPHandler:
    """
    Punto de entrada unificado para todas las operaciones PGP.
    Combina PGPKeyManager, PGPEncryptor, PGPSigner y PGPMIMEBuilder.

    Uso típico:
        handler = PGPHandler()
        handler.key_manager.generate_key("Alice", "alice@local.dev", "pass")
        msg = handler.mime.build_encrypted(
            sender     = "alice@local.dev",
            recipients = ["bob@local.dev"],
            subject    = "Mensaje secreto",
            body       = "Hola Bob",
            passphrase = "pass",
            sign       = True,
        )
    """

    def __init__(self, keys_dir: str = "security/keys"):
        self.key_manager = PGPKeyManager(keys_dir)
        self.encryptor   = PGPEncryptor(self.key_manager)
        self.signer      = PGPSigner(self.key_manager)
        self.mime        = PGPMIMEBuilder(self.key_manager)