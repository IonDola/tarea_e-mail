# Tarea EMail

## Estructura del proyecto

```python
tarea_email/
├── certs/
│   ├── server.crt          # Certificado SSL (autofirmado)
│   └── server.key          # Llave privada SSL
├── gui/                    # Interfaz Grafica (opcional)
│   ├── src/
│   │   ├── components/     # Componentes de la paginas
│   │   ├── styles/         # Estilos css de la paginas
│   │   └── pages/          # Paginas del "correo"
│   ├── public/             # Iconografia
│   ├── dist/               # Compilado de la interfaz con Eel
│   └── package.json
├── mail_storage/
│   ├── users.json          # Usuarios POP3 (user:password)
│   └── <usuario>/          # Correos de cada usuario (.eml + .json)
├── security/               # Cifrado (opcional)
│   ├── pgp_handler.py      # Logica de cifrado
│   └── keys/               # Almacén de llaves PGP
├── smtp/
│   ├── smtpserver.py       # Servidor SMTP
│   ├── smtpclient.py       # Cliente SMTP masivo (CSV)
│   └── config.json         # Configuración del SMTP
├── templates/
│   ├── destinatarios.csv   # Ejemplo de CSV
│   └── bienvenida.txt         # Plantilla de mensaje
├── test/                   # Pruebas
│   ├── smtpserver_test.py
│   ├── smtpclient_test.py
│   ├── pgp_handler_test.py
│   ├── pop3server_test.py
│   └── xmpp_notifier_test.py
├── user/
│   ├── xmpp_config.json    # Configuración XMPP por usuario
│   └── pop3server.py       # Servidor POP3
├── venv/                   # Entorno de desarrollo
├── xmpp/
│   ├── xmpp_notifier.py    # Notificador XMPP
├── gui_main.py             # Inicializador de la Interfaz (opcional)
├── kick-off.pdf            # Pos el kickoff
├── Doxyfile                # Configuración de documentación con Doxygen
└── readme.md               # El presente documento
```

## Dependencias
Dentro del ambiente de desarrollo "venv".

```bash
$ pip install twisted pyopenssl service_identity
```
Si se desea usar la bateria de pruebas.
```bash
pip install pytest twisted pyopenssl
```
Si se desea usar Doxygen.
```bash
pip install doxypypy
```
## Generar certificados SSL (autofirmados)

```bash
openssl req -x509 -newkey rsa:2048 \
  -keyout certs/server.key \
  -out certs/server.crt \
  -days 365 -nodes \
  -subj "/CN=localhost"
```

## SMTP Server

```bash
# Sin SSL (desarrollo)
python smtp/smtpserver.py -d local.dev -s mail_storage -p 2525

# Con SSL
python smtp/smtpserver.py \
  -d local.dev \
  -s mail_storage \
  -p 2525 \
  --cert certs/server.crt \
  --key certs/server.key
```

- `-d`: dominios aceptados (coma-separados)
- `-s`: directorio de almacenamiento
- `-p`: puerto (default 2525)

## SMTP Client

```bash
# Envio de las templates
python smtp/smtpclient.py   -host localhost   -p 2525   -c templates/destinatarios.csv   -m templates/bienvenida.txt   -s noreply@local.dev
```

**CSV formato:**
```
email,nombre
alice@tudominio.com,Alice Pérez
bob@tudominio.com,Bob Ramírez
```

**Variables en el mensaje:**
- `{{nombre}}` → reemplazado con el nombre del CSV
- `{{email}}` → reemplazado con el correo del destinatario

## POP3 Server

```bash
# Sin SSL
python pop3server.py -s ./mail_storage -p 1100

# Con SSL
python pop3server.py -s ./mail_storage -p 9955 \
  --ssl --certfile certs/server.crt --keyfile certs/server.key
```

**Usuarios** — en `mail_storage/users.json`:
```json
{
  "alice": "password123",
  "bob": "secret"
}
```
**Inicio de Sesión de Ejemplo Basico**
```bash
telnet localhost 1100
USER alice
PASS password123
LIST
QUIT

```

## XMPP Notifier

Editar `xmpp_config.json`:
```json
{
  "jid": "notifier@jabber.org",
  "password": "tu_password",
  "notify_jid": "tuusuario@jabber.org"
}
```

El notificador se activa automáticamente al llegar un correo al SMTP Server.
También puede invocarse de forma standalone:

```bash
python xmpp_notifier.py \
  --to alice@tudominio.com \
  --from remitente@otro.com \
  --subject "Nuevo mensaje de prueba"
```

## Prueba integrada rápida

```bash
# Terminal 1: levantar SMTP Server
python smtpserver.py -d localhost -s ./mail_storage -p 2525

# Terminal 2: levantar POP3 Server
python pop3server.py -s ./mail_storage -p 1100

# Terminal 3: enviar correos masivos
python smtpclient.py -host localhost:2525 -c destinatarios.csv -m mensaje.txt

# Verificar correos guardados
ls mail_storage/alice/
ls mail_storage/bob/
```
## Bateria de Pruebas
```bash
python -m pytest test/smtpserver_test.py -v
python -m pytest test/smtpclient_test.py -v
python -m pytest test/pop3server_test.py -v
python -m pytest test/xmpp_notifier_test.py -v
```
## Puntos Extra
### Modo PGP
#### Dependencias
```bash
pip install python-gnupg
sudo apt install gnupg2

```

## Notas técnicas

- Los correos se almacenan como `.eml` (raw MIME) + `.json` (metadata).
- El POP3 server elimina físicamente los `.eml` al hacer `sync()` (disconnect).
- El SMTP server valida el dominio del destinatario; rechaza con `SMTPBadRcpt` si no coincide.
- SSL/TLS usa certificados OpenSSL a través de `twisted.internet.ssl`.
- El XMPP notifier corre en un hilo separado para no bloquear el reactor de Twisted.
- En el .gitignore se omite la información producida por Doxygen, pues se considera redundante subirla al repositorio
