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
│   └── mensaje.txt         # Plantilla de mensaje
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
└── readme.md               # El presente documento
```

## Dependencias
Dentro del ambiente de desarrollo "venv".

```bash
pip install twisted slixmpp pyopenssl service_identity
```

## Generar certificados SSL (autofirmados)

```bash
openssl req -x509 -newkey rsa:2048 \
  -keyout certs/server.key \
  -out certs/server.crt \
  -days 365 -nodes -subj "/CN=tudominio.com"
```

## SMTP Server

```bash
# Sin SSL (desarrollo)
python smtpserver.py -d localhost,tudominio.com -s ./mail_storage -p 2525

# Con SSL
python smtpserver.py -d tudominio.com -s ./mail_storage -p 465 \
  --ssl --certfile certs/server.crt --keyfile certs/server.key
```

- `-d`: dominios aceptados (coma-separados)
- `-s`: directorio de almacenamiento
- `-p`: puerto (default 2525)

## SMTP Client

```bash
# Envío básico
python smtpclient.py -H localhost:2525 -c destinatarios.csv -m mensaje.txt

# Con remitente personalizado y adjunto
python smtpclient.py -H localhost:2525 -c destinatarios.csv -m mensaje.txt \
  --from yo@tudominio.com --attach archivo.pdf

# Con SSL
python smtpclient.py -H tudominio.com:465 -c destinatarios.csv -m mensaje.txt --ssl
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

**Usuarios** — editar `mail_storage/users.json`:
```json
{
  "alice": "password123",
  "bob": "password456"
}
```

**Thunderbird** — configurar cuenta POP3:
- Servidor: `localhost` (o tu dominio)
- Puerto: `1100` (o `9955` con SSL)
- Usuario: `alice`
- Contraseña: `password123`
- SSL: ninguno / SSL según configuración

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
python smtpclient.py -H localhost:2525 -c destinatarios.csv -m mensaje.txt

# Verificar correos guardados
ls mail_storage/alice/
ls mail_storage/bob/
```

## Notas técnicas

- Los correos se almacenan como `.eml` (raw MIME) + `.json` (metadata).
- El POP3 server elimina físicamente los `.eml` al hacer `sync()` (disconnect).
- El SMTP server valida el dominio del destinatario; rechaza con `SMTPBadRcpt` si no coincide.
- SSL/TLS usa certificados OpenSSL a través de `twisted.internet.ssl`.
- El XMPP notifier corre en un hilo separado para no bloquear el reactor de Twisted.
