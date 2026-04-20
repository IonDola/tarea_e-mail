import os
import sys
import json
import glob
import hashlib
import argparse

from twisted.internet import reactor, ssl, defer
from twisted.protocols import basic
from twisted.internet  import protocol
from twisted.python    import log


## @package user.pop3server
# Servidor POP3 que lee correos desde el sistema de archivos.
# Cada usuario tiene un directorio en mail_storage/<usuario>/ con archivos .eml (correo) y .json (metadatos).
# El servidor soporta comandos básicos de POP3: USER, PASS, STAT, LIST, RETR, DELE, NOOP, RSET, UIDL, TOP y QUIT.
# El archivo mail_storage/users.json contiene las credenciales de los usuarios en formato JSON:
# Uso:
# Terminal 1: python user/pop3server.py -s mail_storage -p 1100
# Terminal 2: telnet localhost 1100
class POP3Mailbox:
    """
    @class POP3Mailbox
        Representa el buzón de un usuario.
        Lee los archivos .eml y .json del directorio mail_storage/<usuario>/ y expone operaciones RETR, DELE, LIST, STAT compatibles con POP3.
    """

    def __init__(self, user_dir: str):
        self.user_dir = user_dir
        self._messages: list[dict] = []   # [{path_eml, path_meta, size, deleted}]
        self._load()

    # --- Carga inicial ---

    def _load(self) -> None:
        """
        @method _load
            Lee los archivos .eml del directorio del usuario y construye la lista de mensajes.
        """
        pattern = os.path.join(self.user_dir, "*.eml")
        for eml_path in sorted(glob.glob(pattern)):
            meta_path = eml_path.replace(".eml", ".json")
            size      = os.path.getsize(eml_path)
            self._messages.append({
                "path_eml":  eml_path,
                "path_meta": meta_path,
                "size":      size,
                "deleted":   False,
            })

    # --- API POP3 (índices base-1 como exige el protocolo) ---

    def stat(self) -> tuple[int, int]:
        """
        @method stat
            @return (cantidad de mensajes, bytes_totales)
            Retorna (cantidad, bytes_totales) de mensajes no borrados."""
        active = [m for m in self._messages if not m["deleted"]]
        total  = sum(m["size"] for m in active)
        return len(active), total

    def list_messages(self) -> list[tuple[int, int]]:
        """
        @method list_messages
            @return list[tuple[int, int]]
            Retorna lista de (numero, tamaño) para mensajes no borrados.
        """
        result = []
        num = 1
        for m in self._messages:
            if not m["deleted"]:
                result.append((num, m["size"]))
            num += 1
        return result

    def get_message(self, number: int) -> bytes | None:
        """
        @method get_message
            @return bytes | None
            Retorna el contenido crudo del mensaje número <number> (base-1).
            Retorna None si el número es inválido o el mensaje está marcado como borrado."""
        msg = self._get(number)
        if msg is None or msg["deleted"]:
            return None
        with open(msg["path_eml"], "rb") as f:
            return f.read()

    def delete_message(self, number: int) -> bool:
        """
        @method delete_message
            @return bool
            Marca el mensaje como borrado. Retorna True si tuvo éxito.
        """
        msg = self._get(number)
        if msg is None or msg["deleted"]:
            return False
        msg["deleted"] = True
        return True

    def commit_deletes(self) -> None:
        """
        @method commit_deletes
            Elimina físicamente los archivos marcados como borrados.
        """
        for m in self._messages:
            if m["deleted"]:
                for path in (m["path_eml"], m["path_meta"]):
                    if os.path.exists(path):
                        os.remove(path)

    def rollback_deletes(self) -> None:
        """
        @method rollback_deletes
            Deshace las marcas de borrado (usado en RSET y en cierre abrupto).
        """
        for m in self._messages:
            m["deleted"] = False

    def uidl(self, number: int | None = None) -> list[tuple[int, str]]:
        """
        @method uidl
            @return list[tuple[int, str]]
            Retorna lista de (numero, uid) para UIDL.
            El UID se genera como MD5 del nombre del archivo .eml.
        """
        result = []
        num = 1
        for m in self._messages:
            if not m["deleted"]:
                uid = hashlib.md5(
                    os.path.basename(m["path_eml"]).encode()
                ).hexdigest()
                if number is None or num == number:
                    result.append((num, uid))
            num += 1
        return result

    def _get(self, number: int) -> dict | None:
        """
        @method _get
            @return dict | None
            Acceso base-1 seguro.
        """
        if 1 <= number <= len(self._messages):
            return self._messages[number - 1]
        return None

class UserAuth:
    """
    @class UserAuth
        Valida usuarios contra mail_storage/users.json.
        Formato del archivo:
        {<nombre de usuario>: <contraseña>, <nombre de usuario>: <contraseña>, ...}
    """

    def __init__(self, storage_path: str):
        self.users_file = os.path.join(storage_path, "users.json")
        self._users: dict = {}
        self._load()

    def _load(self) -> None:
        """
        @method _load
            Carga las credenciales desde users.json.
        """
        if not os.path.exists(self.users_file):
            log.msg(f"[auth] users.json no encontrado en {self.users_file}")
            return
        with open(self.users_file, "r") as f:
            self._users = json.load(f)

    def validate(self, username: str, password: str) -> bool:
        """
        @method validate
            @return bool
            Valida si el par (username, password) es correcto.
        """
        return self._users.get(username) == password

    def user_exists(self, username: str) -> bool:
        """
        @method user_exists
            @return bool
            Verifica si un usuario existe.
        """
        return username in self._users

class POP3Protocol(basic.LineReceiver):
    """
    @class POP3Protocol
        Implementación del protocolo POP3 (RFC 1939).
        Estados: AUTHORIZATION → TRANSACTION → UPDATE
    """

    delimiter = b"\r\n"

    # Estados
    STATE_AUTH        = "AUTHORIZATION"
    STATE_TRANSACTION = "TRANSACTION"
    STATE_UPDATE      = "UPDATE"

    def __init__(self, storage_path: str, auth: UserAuth):
        self.storage_path = storage_path
        self.auth         = auth
        self.state        = self.STATE_AUTH
        self.username     = None
        self.mailbox: POP3Mailbox | None = None

    def connectionMade(self):
        """
        @method connectionMade
            Envia el mensaje de bienvenida al cliente.
        """
        self._ok("POP3 server ready")

    def connectionLost(self, reason):
        """
        @method connectionLost
            Maneja la pérdida de conexión con el cliente.
        """
        if self.mailbox and self.state == self.STATE_TRANSACTION:
            self.mailbox.rollback_deletes()

    def lineReceived(self, line: bytes):
        """
        @method lineReceived
            Procesa una línea de comando recibida del cliente.
            Decodifica, parsea el comando y llama al handler correspondiente.
        """
        try:
            text = line.decode("utf-8", errors="replace").strip()
        except Exception:
            self._err("Encoding error")
            return

        parts   = text.split(" ", 1)
        command = parts[0].upper()
        arg     = parts[1] if len(parts) > 1 else ""

        handler = getattr(self, f"_cmd_{command}", None)
        if handler:
            handler(arg)
        else:
            self._err(f"Unknown command: {command}")

    def _cmd_USER(self, arg: str):
        """
        @method _cmd_USER
            Maneja el comando USER <username>.
             - Solo válido en estado AUTHORIZATION.
             - Almacena el nombre de usuario para validación posterior con PASS.
        """
        if self.state != self.STATE_AUTH:
            self._err("Already authenticated")
            return
        if not arg:
            self._err("Usage: USER <username>")
            return
        self.username = arg
        self._ok(f"Hello {arg}")

    def _cmd_PASS(self, arg: str):
        """
        @method _cmd_PASS
            Maneja el comando PASS <password>.
             - Solo válido en estado AUTHORIZATION.
             - Requiere que USER ya haya sido enviado.
             - Valida las credenciales usando UserAuth.
             - Si son válidas, cambia al estado TRANSACTION y carga el buzón del usuario.
        """
        if self.state != self.STATE_AUTH:
            self._err("Already authenticated")
            return
        if not self.username:
            self._err("Send USER first")
            return
        if not self.auth.validate(self.username, arg):
            self.username = None
            self._err("Invalid credentials")
            return

        user_dir = os.path.join(self.storage_path, self.username)
        os.makedirs(user_dir, exist_ok=True)
        self.mailbox = POP3Mailbox(user_dir)
        self.state   = self.STATE_TRANSACTION
        count, size  = self.mailbox.stat()
        self._ok(f"Logged in. {count} messages ({size} octets)")

    def _cmd_STAT(self, arg: str):
        """
        @method _cmd_STAT
            Maneja el comando STAT.
            - Solo válido en estado TRANSACTION.
            - Retorna la cantidad de mensajes y el tamaño total en octetos.
        """
        if not self._require_transaction():
            return
        count, size = self.mailbox.stat()
        self._ok(f"{count} {size}")

    def _cmd_LIST(self, arg: str):
        """
        @method _cmd_LIST
            Maneja el comando LIST.
            - Solo válido en estado TRANSACTION.
            - Retorna una lista de todos los mensajes con su número y tamaño.
        """

        if not self._require_transaction():
            return
        if arg:
            try:
                num  = int(arg)
                msgs = self.mailbox.list_messages()
                hit  = next((m for m in msgs if m[0] == num), None)
                if hit:
                    self._ok(f"{hit[0]} {hit[1]}")
                else:
                    self._err(f"No such message: {num}")
            except ValueError:
                self._err("Invalid message number")
        else:
            msgs = self.mailbox.list_messages()
            self._ok(f"{len(msgs)} messages")
            for num, size in msgs:
                self.sendLine(f"{num} {size}".encode())
            self.sendLine(b".")

    def _cmd_RETR(self, arg: str):
        """
        @method _cmd_RETR
            Maneja el comando RETR <num>.
            - Solo válido en estado TRANSACTION.
            - Retorna el contenido del mensaje número <num>.
             - Si el mensaje no existe o está marcado como borrado, retorna error.
             - El contenido se envía con byte-stuffing (líneas que empiezan con "." → "..") y termina con una línea "." sola.
        """
        if not self._require_transaction():
            return
        try:
            num  = int(arg)
            data = self.mailbox.get_message(num)
            if data is None:
                self._err(f"No such message: {num}")
                return
            self._ok(f"{len(data)} octets")
            for line in data.splitlines():
                if line.startswith(b"."):
                    line = b"." + line
                self.sendLine(line)
            self.sendLine(b".")
        except ValueError:
            self._err("Invalid message number")

    def _cmd_DELE(self, arg: str):
        """
        @method _cmd_DELE
        Maneja el comando DELE <num>.
        - Solo válido en estado TRANSACTION.
        - Marca el mensaje número <num> como borrado.
        - Si el mensaje no existe o ya está marcado como borrado, retorna error.
        - Los mensajes marcados como borrados serán eliminados físicamente al finalizar la sesión (QUIT) o si se pierde la conexión sin hacer RSET.
        - El comando DELE no elimina inmediatamente el mensaje, solo lo marca para eliminación.
        - El cliente puede usar RSET para deshacer las marcas de borrado antes de finalizar la sesión.
        """
        if not self._require_transaction():
            return
        try:
            num = int(arg)
            if self.mailbox.delete_message(num):
                self._ok(f"Message {num} deleted")
            else:
                self._err(f"No such message: {num}")
        except ValueError:
            self._err("Invalid message number")

    def _cmd_NOOP(self, arg: str):
        """
        @method _cmd_NOOP
            Maneja el comando NOOP.
            - Solo válido en estado TRANSACTION.
            - No realiza ninguna acción, solo responde con OK.
        """
        if not self._require_transaction():
            return
        self._ok("")

    def _cmd_RSET(self, arg: str):
        """
        @method _cmd_RSET
            Maneja el comando RSET.
            - Solo válido en estado TRANSACTION.
            - Deshace todas las marcas de borrado hechas por DELE en esta sesión.
            - Después de RSET, los mensajes marcados como borrado vuelven a estar disponibles.
            - El comando RSET no afecta a los mensajes que ya fueron eliminados físicamente (si se hizo QUIT o se perdió la conexión sin hacer RSET).
        """
        if not self._require_transaction():
            return
        self.mailbox.rollback_deletes()
        count, size = self.mailbox.stat()
        self._ok(f"Maildrop has {count} messages ({size} octets)")

    def _cmd_UIDL(self, arg: str):
        """
        @method _cmd_UIDL
            Maneja el comando UIDL [<num>].
            - Solo válido en estado TRANSACTION.
            - Si se proporciona <num>, retorna el número y UID del mensaje específico.
            - Si no se proporciona <num>, retorna la lista de números y UIDs de todos los mensajes no borrados.
            - El UID se genera como un hash (MD5) del nombre del archivo .eml para garantizar unicidad y persistencia entre sesiones.
        """
        if not self._require_transaction():
            return
        if arg:
            try:
                num    = int(arg)
                result = self.mailbox.uidl(num)
                if result:
                    self._ok(f"{result[0][0]} {result[0][1]}")
                else:
                    self._err(f"No such message: {num}")
            except ValueError:
                self._err("Invalid message number")
        else:
            result = self.mailbox.uidl()
            self._ok("")
            for num, uid in result:
                self.sendLine(f"{num} {uid}".encode())
            self.sendLine(b".")

    def _cmd_TOP(self, arg: str):
        """
        @method _cmd_TOP
            Maneja el comando TOP <num> <lines>.
            - Solo válido en estado TRANSACTION.
             - Retorna los encabezados del mensaje número <num> y las primeras <lines> líneas del cuerpo.
             - Si el mensaje no existe o está marcado como borrado, retorna error.
             - El contenido se envía con byte-stuffing (líneas que empiezan con "." → "..") y termina con una línea "." sola.
        """
        if not self._require_transaction():
            return
        parts = arg.split()
        if len(parts) != 2:
            self._err("Usage: TOP <num> <lines>")
            return
        try:
            num   = int(parts[0])
            lines = int(parts[1])
            data  = self.mailbox.get_message(num)
            if data is None:
                self._err(f"No such message: {num}")
                return

            # Separar encabezados del cuerpo
            decoded = data.decode("utf-8", errors="replace")
            if "\r\n\r\n" in decoded:
                headers, body = decoded.split("\r\n\r\n", 1)
            else:
                headers, body = decoded, ""

            body_lines = body.splitlines()[:lines]
            output     = headers + "\r\n\r\n" + "\r\n".join(body_lines)

            self._ok("")
            for line in output.encode().splitlines():
                if line.startswith(b"."):
                    line = b"." + line
                self.sendLine(line)
            self.sendLine(b".")
        except ValueError:
            self._err("Invalid arguments")

    def _cmd_QUIT(self, arg: str):
        """
        @method _cmd_QUIT
            Maneja el comando QUIT.
            - En estado TRANSACTION, primero elimina físicamente los mensajes marcados como borrados. Luego responde con OK y cierra la conexión.
            - En estado AUTHORIZATION, simplemente responde con OK y cierra la conexión.
        """
        if self.state == self.STATE_TRANSACTION:
            self.mailbox.commit_deletes()
            self.state = self.STATE_UPDATE
        self._ok("Goodbye")
        self.transport.loseConnection()

    # --- Helpers ---

    def _ok(self, msg: str):
        self.sendLine(f"+OK {msg}".encode())

    def _err(self, msg: str):
        self.sendLine(f"-ERR {msg}".encode())

    def _require_transaction(self) -> bool:
        if self.state != self.STATE_TRANSACTION:
            self._err("Not authenticated")
            return False
        return True

class POP3Factory(protocol.ServerFactory):
    """
    @class POP3Factory
        Fábrica de protocolos para el servidor POP3.
    """
    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self.auth         = UserAuth(storage_path)

    def buildProtocol(self, addr):
        """
        @method buildProtocol
            Construye una instancia de POP3Protocol para cada nueva conexión.
        """
        return POP3Protocol(self.storage_path, self.auth)


def _parse_args():
    """
    @function _parse_args
        Parsea los argumentos de línea de comandos para configurar el servidor POP3.
        -s, --storage: Directorio raíz de almacenamiento de correos (requerido).
        -p, --port: Puerto de escucha (default: 1100).
        --cert: Certificado SSL (default: certs/server.crt).
        --key: Llave privada SSL (default: certs/server.key).
        --ssl: Usar SSL/TLS nativo (POP3S, puerto 995).
        
        @return Objeto con los argumentos parseados.

        Si se especifica --ssl, el servidor intentará usar SSL/TLS nativo. Si los archivos de certificado o llave no existen, caerá a modo sin SSL.
    """
    parser = argparse.ArgumentParser(description="Servidor POP3 con Twisted")
    parser.add_argument("-s", "--storage", required=True,
                        help="Directorio raíz de almacenamiento de correos")
    parser.add_argument("-p", "--port",    type=int, default=1100,
                        help="Puerto de escucha (default: 1100)")
    parser.add_argument("--cert",          default="certs/server.crt",
                        help="Certificado SSL")
    parser.add_argument("--key",           default="certs/server.key",
                        help="Llave privada SSL")
    parser.add_argument("--ssl",           action="store_true",
                        help="Usar SSL/TLS nativo (POP3S, puerto 995)")
    return parser.parse_args()


def main():
    """
    @function main
        Punto de entrada del servidor POP3.
        - Parsea los argumentos de línea de comandos.
        - Configura el logging para mostrar mensajes en la consola.
        - Crea una instancia de POP3Factory con la ruta de almacenamiento especificada.
        - Si se especifica --ssl y los archivos de certificado y llave existen, configura el servidor para usar SSL/TLS nativo en el puerto especificado. De lo contrario, inicia el servidor sin SSL en el puerto especificado.
        - Inicia el reactor de Twisted para comenzar a aceptar conexiones entrantes.
    """
    log.startLogging(sys.stdout)
    args    = _parse_args()
    factory = POP3Factory(args.storage)

    if args.ssl and os.path.exists(args.cert) and os.path.exists(args.key):
        ctx = ssl.DefaultOpenSSLContextFactory(args.key, args.cert)
        reactor.listenSSL(args.port, factory, ctx)
        log.msg(f"[POP3S] Escuchando en puerto {args.port} (SSL)")
    else:
        reactor.listenTCP(args.port, factory)
        log.msg(f"[POP3] Escuchando en puerto {args.port}")

    reactor.run()


if __name__ == "__main__":
    main()