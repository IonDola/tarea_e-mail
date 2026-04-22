# Documentación — Tarea e-Mail

**Curso:** Redes — Ingeniería de Computación, Plan 411  
**Profesor:** Kevin Moraga  
**Estudiante:** Ion Ángel Dolanescu Bravo  
**Carné:** 2022049034  
**Repositorio:** https://github.com/IonDola/tarea_e-mail  

---

## 1. Introducción

El correo electrónico es uno de los servicios fundamentales de Internet. Está basado en el protocolo SMTP (Simple Mail Transfer Protocol), diseñado originalmente para emular de forma digital el correo postal tradicional. A pesar de su antigüedad, sigue siendo uno de los protocolos más utilizados en la industria, únicamente superado en popularidad por HTTP.

El problema central de esta tarea consiste en construir desde cero un sistema de correo electrónico funcional, compuesto por un servidor SMTP capaz de recibir y almacenar correos, un cliente SMTP para envío masivo personalizado, un servidor POP3 para la descarga de correos, y un notificador XMPP que alerte al usuario cuando llega un mensaje nuevo.

La estrategia de solución divide el sistema en cuatro componentes independientes que se comunican a través del almacenamiento compartido en `mail_storage/`:

```
SMTP-Client ──► SMTP-Server ──► mail_storage/ ──► POP3-Server ──► Thunderbird
                     │
                     └──► XMPP-Notifier ──► Usuario XMPP
```

Cada componente se implementó como un módulo Python independiente, con su propia batería de pruebas, lo que facilita el desarrollo, la depuración y la verificación individual de cada parte del sistema.

---

## 2. Ambiente de Desarrollo

### Sistema operativo y lenguaje

El desarrollo se realizó sobre **Ubuntu 24.04 LTS** usando **Python 3.12** como lenguaje principal, cumpliendo el requisito de GNU/Linux especificado en el enunciado.

### Herramientas utilizadas

| Herramienta | Versión | Uso |
|---|---|---|
| Python | 3.12 | Lenguaje principal |
| pyinstaller |6.19.0 | Generador de Binarios |
| Twisted | 25.5.0 | Implementación de servidores SMTP y POP3 |
| pyOpenSSL | 26.0.0 | Soporte SSL/TLS |
| service_identity | 24.2.0 | Verificación de identidad TLS |
| python-gnupg | 0.5.6 | Cifrado PGP (módulo presente, no integrado) |
| Eel | 0.18.2 | Interfaz gráfica Python + HTML/JS |
| pytest | 9.0.3 | Batería de pruebas unitarias |
| doxypypy | 0.8.8.7 | Generación de documentación desde docstrings |
| Doxygen | 1.9.8 | Generación de documentación automatizada |
| pyOpenSSL | 26.0.0 | Generación de certificados autofirmados |
| Thunderbird | 149.0.2 | Cliente POP3 para pruebas manuales |
| Visual Studio Code | 1.106.0 | Editor de código principal |
| Git | 2.43.0 | Control de versiones |
| GitHub | N/A | Repositorio de versiones |

### Entorno virtual

Todas las dependencias se gestionan dentro de un entorno virtual `venv` para aislar el proyecto del sistema, requirements.txt conserva sus respectivas versiones:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Forma de debugging

El debugging se realizó con tres mecanismos complementarios:

- **Logs de Twisted** (`twisted.python.log`) visibles en stdout al correr los servidores.
- **Telnet manual** para probar los protocolos SMTP y POP3 comando por comando.
- **pytest con flag `-v`** para identificar exactamente qué test falla y por qué.

### Flujo de trabajo

```
feature branch ──► pruebas locales ──► commit ──► merge a main
```

Se utilizaron commits frecuentes con mensajes descriptivos. La rama `main` siempre contiene código funcional; el desarrollo activo se realizó en la rama `dev`.

---

## 3. Estructuras de Datos y Funciones Principales

### 3.1 `smtp/smtpserver.py`

#### `MaildirStorage`

Gestiona la persistencia de correos en disco. Cada correo se almacena como dos archivos en `mail_storage/<usuario>/`:

- `<timestamp>.eml` — mensaje crudo en formato MIME
- `<timestamp>.json` — metadatos del correo

```python
{
  "from":      "remitente@dominio.com",
  "to":        "destinatario@local.dev",
  "timestamp": "20260421_143022_123456",
  "read":      false,
  "path":      "mail_storage/alice/20260421_143022_123456.eml"
}
```

Método principal: `save(recipient, sender, raw_message)` — crea ambos archivos atómicamente.

#### `SMTPDelivery`

Implementa `IMessageDelivery` de Twisted. Contiene la política de dominios aceptados: `validateTo()` rechaza con `SMTPBadRcpt` cualquier destinatario cuyo dominio no esté en la lista configurada con `-d`.

#### `SMTPMessage`

Implementa `IMessage` de Twisted. Acumula líneas del cuerpo del correo en una lista durante la transmisión y las persiste al recibir `eomReceived()`. En caso de desconexión abrupta, `connectionLost()` descarta el buffer sin persistir.

#### `SMTPFactory`

Fábrica de Twisted que usa `smtp.ESMTP` (en lugar de `smtp.SMTP` básico) para habilitar la extensión `STARTTLS`. Inyecta el contexto SSL opcionalmentey crea una instancia de `SMTPDelivery` por conexión.

---

### 3.2 `smtp/smtpclient.py`

#### `RecipientLoader`

Lee el CSV de destinatarios con `csv.DictReader`. Valida que existan las columnas `email` y `nombre` y normaliza todas las claves a minúsculas. Omite filas con email vacío sin lanzar error.

#### `TemplateRenderer`

Motor de plantillas basado en expresiones regulares. Sustituye variables con sintaxis `{{nombre_variable}}` usando las columnas del CSV como diccionario de variables. Variables no encontradas se conservan sin cambio para facilitar el debug.

```python
render("Hola {{nombre}}", {"nombre": "Alice"})  # → "Hola Alice"
```

#### `MessageLoader`

Parsea el archivo de plantilla separando encabezados del cuerpo en la primera línea en blanco, igual que el formato de un correo real. Soporta los encabezados `Subject:` y `Attachment:`.

#### `EmailBuilder`

Construye objetos `MIMEMultipart` listos para enviar. Aplica `TemplateRenderer` tanto al asunto como al cuerpo. El adjunto se codifica en base64 automáticamente con `encoders.encode_base64`.

#### `SMTPSender`

Abre una única conexión SMTP para enviar todos los correos de la lista en secuencia. Soporta STARTTLS y autenticación. Retorna un resumen `{"ok": [...], "failed": [...]}` con el resultado por destinatario.

---

### 3.3 `user/pop3server.py`

#### `POP3Mailbox`

Representa el buzón de un usuario. Carga todos los `.eml` del directorio del usuario al autenticarse y expone operaciones con índices base-1 como exige RFC 1939:

| Método | Comando POP3 |
|---|---|
| `stat()` | STAT |
| `list_messages()` | LIST |
| `get_message(n)` | RETR |
| `delete_message(n)` | DELE |
| `commit_deletes()` | (al QUIT) |
| `rollback_deletes()` | RSET / cierre abrupto |
| `uidl(n)` | UIDL |

Los borrados son diferidos — `delete_message()` solo marca el mensaje, `commit_deletes()` elimina físicamente los archivos al hacer `QUIT`.

#### `UserAuth`

Valida credenciales contra `mail_storage/users.json`. Completamente desacoplado del protocolo para facilitar cambiar el backend de autenticación.

#### `POP3Protocol`

Implementa la máquina de estados del RFC 1939:

```
AUTHORIZATION ──► TRANSACTION ──► UPDATE
```

Cada comando POP3 es un método `_cmd_NOMBRE()` que el dispatcher encuentra con `getattr`. Esto hace trivial agregar comandos nuevos sin modificar el dispatcher.

#### `POP3Factory`

Crea una instancia de `POP3Protocol` por conexión, compartiendo el mismo objeto `UserAuth` entre todas las conexiones.

---

### 3.4 `xmpp/xmpp_notifier.py`

#### `MailboxMonitor`

Monitorea `mail_storage/<usuario>/` buscando archivos `.json` con `read: false`. Mantiene un conjunto interno de archivos ya notificados para que cada correo genere solo una alerta.

#### `NotificationFormatter`

Genera el texto de notificación XMPP completamente desacoplado del protocolo. Produce mensajes como:

```
Tenés 2 correos sin leer.
📧 Nuevo correo de bob@ext.com — Asunto de prueba [20260421_143022]
```

#### `XMPPNotifierHandler`

Extiende `XMPPHandler` de Twisted Words. Gestiona el ciclo de vida de la sesión XMPP: envía presencia al conectarse y construye stanzas `<message type="chat">` con `domish`. El flag `_ready` evita intentar enviar antes de que la sesión esté autenticada.

#### `XMPPNotifierService`

Orquesta todo el flujo con un `LoopingCall` de Twisted que ejecuta `_check_mailbox()` cada N segundos (configurable). Si la sesión XMPP no está disponible al momento de la notificación, el correo se devuelve al pool para reintentarlo en el siguiente ciclo.

---

### 3.5 `gui_main.py`

Backend Python de la interfaz gráfica. Expone funciones al frontend JavaScript mediante el decorador `@eel.expose`:

| Función expuesta | Descripción |
|---|---|
| `login(user, pass)` | Valida contra `users.json` |
| `logout()` | Limpia la sesión activa |
| `get_session()` | Estado actual de sesión |
| `get_inbox()` | Lista metadatos de correos del usuario |
| `get_email(id)` | Contenido completo + marca como leído |
| `delete_email(id)` | Elimina `.eml` y `.json` |
| `send_email(to, subject, body)` | Envío simple via SMTP |
| `send_bulk(csv, msg)` | Envío masivo desde la GUI |
| `save_smtp_config(host, port, tls)` | Persiste configuración en `config.json` |
| `get_smtp_config()` | Lee configuración actual |

La función `_extract_body()` parsea el contenido MIME correctamente manejando texto plano, multipart, y PGP/MIME cifrado.

---

## 4. Instrucciones para Ejecutar el Programa

### Preparación del entorno

```bash
# Clonar el repositorio
git clone https://github.com/IonDola/tarea_e-mail.git
cd tarea_e-mail

# Crear y activar entorno virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

### Generar certificados SSL

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 \
  -keyout certs/server.key \
  -out certs/server.crt \
  -days 365 -nodes \
  -subj "/CN=localhost"
```

### Crear usuarios

Editar `mail_storage/users.json`:

```json
{
  "alice": "pass123",
  "bob": "secret"
}
```

### Levantar el sistema completo (3 terminales)

```bash
# Terminal 1 — SMTP Server
python smtp/smtpserver.py \
  -d local.dev \
  -s mail_storage \
  -p 2525 \
  --cert certs/server.crt \
  --key certs/server.key

# Terminal 2 — POP3 Server
python user/pop3server.py \
  -s mail_storage \
  -p 1100 \
  --ssl \
  --cert certs/server.crt \
  --key certs/server.key

# Terminal 3 — XMPP Notifier
python xmpp/xmpp_notifier.py --config user/xmpp_config.json
```

### Enviar correos con el cliente SMTP

```bash
# Envío local (contra smtpserver.py)
python smtp/smtpclient.py \
  -host localhost \
  -p 2525 \
  -c templates/destinatarios.csv \
  -m templates/bienvenida.txt \
  -s noreply@local.dev
```
## 5. Compilado y Uso con Thunderbird
### Compilado
Unicamente estos archivos toleran pasar al formato de binario, poder realizarlo con los otros resulta con mayor complegidad.
```bash
pyinstaller --onefile smtp/smtpserver.py 
pyinstaller --onefile smtp/smtpclient.py 
pyinstaller --onefile user/pop3server.py
```

### Configurar Thunderbird como cliente POP3

Thunderbird permite verificar el funcionamiento del servidor POP3 con un
cliente de correo real estándar.

**Requisito previo:** tener el POP3 server corriendo:

```bash
./dist/pop3server -s mail_storage -p 1100
```

**Pasos de configuración:**

1. Abrir Thunderbird → Menú → Configuración de cuentas → Agregar cuenta de correo
2. Ingresar los datos de la cuenta:

```
Tu nombre:    Alice
Correo:       alice@local.dev
Contraseña:   pass123
```
3. Thunderbird, detecta una anomalia con la cuenta y nos permite seleccionar la opcion de "Configurar manualmente", al seleccionarlo ingresamos los siguientes datos
```
Servidor entrante
─────────────────
Protocolo:    POP3
Servidor:     localhost
Puerto:       1100
Seguridad:    Ninguna  (o SSL/TLS si usás --ssl)
Autenticación: Contraseña normal
Usuario:      alice

Servidor saliente (SMTP)
────────────────────────
Servidor:     localhost
Puerto:       2525
Seguridad:    Ninguna
Autenticación: Ninguna
Usuario:      alice
```
5. Confirmar y guardar.
6. (No obligatorio) Puedes alternar si Thunderbird elimina los correos fisicos de /mail_storage desde Configuración de la cuenta → Configuración del servidor

### Sesión POP3 manual con telnet
```bash
telnet localhost 1100
USER alice
PASS password123
STAT
LIST
RETR 1
DELE 1
QUIT
```

### Prueba de Actualización de Bandeja
```bash
# Terminal 1 — SMTP Server
./dist/smtpserver -d local.dev -s mail_storage -p 2525

# Terminal 2 — POP3 Server
./dist/pop3server -s mail_storage -p 1100

# Terminal 3 — Enviar correo spam de prueba
./dist/smtpclient \
  -host localhost \
  -p 2525 \
  -c templates/destinatarios.csv \
  -m templates/bienvenida.txt \
  -s bob@local.dev
```

Luego en Thunderbird presionar **Obtener mensajes** — el correo enviado por
Bob aparece en la bandeja de Alice descargado via POP3.

## 7. Interfaz gráfica
Corresponde a la interfaz generada para el porcentaje extra
```bash
python gui_main.py
# Abre automáticamente en http://localhost:8686
```

## 8. Batería de pruebas

```bash
# Todas las pruebas
python -m pytest test/ -v

# Por módulo
python -m pytest test/smtpserver_test.py -v
python -m pytest test/smtpclient_test.py -v
python -m pytest test/pop3server_test.py -v
python -m pytest test/xmpp_notifier_test.py -v
```
## 8. Generar Doxygen
Toma todos los archivos del proyecto y produce la documentacion de las clases de python.
```
doxygen Doxyfile
```

---

## 9. Actividades Realizadas

| Fecha de Inicio | Actividad | Horas |
|---|---|---|
| 2026-04-17 | Lectura del enunciado, kick-off y planificación del sistema | 2 |
| 2026-04-18 | Investigación de Twisted SMTP — `IMessage`, `IMessageDelivery`, `SMTPFactory` | 3 |
| 2026-04-18 | Implementación de `smtpserver.py` con `MaildirStorage` y validación de dominios | 4 |
| 2026-04-18 | Escritura de baterías de pruebas para el modulo `smtpserver.py`| 2 |
| 2026-04-18 | Implementación de `smtpclient.py` con motor de plantillas y soporte CSV | 3 |
| 2026-04-19 | Pruebas manuales SMTP con telnet, corrección de bugs en `SMTPDelivery` | 1 |
| 2026-04-19 | Escritura de baterías de pruebas para el modulo `smtpclient.py`| 1 |
| 2026-04-19 | Investigación RFC 1939, implementación de `pop3server.py` con máquina de estados | 3 |
| 2026-04-19 | Implementación de `xmpp_notifier.py` con `LoopingCall` y `XMPPHandler` | 2 |
| 2026-04-19 | Escritura de baterías de pruebas para el modulo `xmpp_notifier.py` | 1 |
| 2026-04-19 | Implementación de SSL/TLS en SMTP y POP3 con `pyOpenSSL` | 2 |
| 2026-04-19 | Implementación de la GUI con Eel — login, bandeja, redactar, configuración | 2 |
| 2026-04-21 | Corrección de bugs en parsing MIME (`_extract_body`), integración GUI-backend | 3 |
| 2026-04-21 | Pruebas de uso de Thunderbird | 1 |
| 2026-04-21 | Pruebas de envío de emails y servidores externos, documentación | 2 |
| **Total** | | **33** |

---

## 10. Autoevaluación

### Estado final del programa

El sistema de correo electrónico quedó completamente funcional en todos los componentes obligatorios. A continuación el estado de cada ítem:

| Componente | Estado | Observaciones |
|---|---|---|
| SMTP Server |  Completo | Soporta STARTTLS, validación de dominios, adjuntos MIME |
| SMTP Client |  Completo | CSV, plantillas `{{variable}}`, STARTTLS, servidores externos |
| POP3 Server | Completo | RFC 1939 completo, SSL/TLS, autenticación, commit/rollback |
| XMPP Notifier |  Completo | Polling configurable, notificación por correo nuevo |
| SSL/TLS | Parcialmente Funcional | STARTTLS en SMTP, SSL nativo en POP3 |
| GUI (opcional) | Completo | Login, bandeja, redactar, envío masivo, configuración |
| PGP/MIME (opcional) | Descartado | Módulo implementado pero no integrado al flujo principal |
| Dominio real | ❌ No completado | Se esperó aprobación del GitHub Student Pack, no llegó a tiempo |

### Problemas encontrados

**Reactor de Twisted no reiniciable** — Durante las pruebas de integración se descubrió que el reactor de Twisted no puede reiniciarse una vez detenido. Esto causaba que tests de integración consecutivos se congelaran. La solución fue usar fixtures con `scope="module"` en pytest para levantar el servidor una sola vez por módulo de test.

**Base64 en partes MIME** — `MIMEText` codifica el cuerpo en base64 automáticamente cuando contiene caracteres UTF-8. Esto causaba que `get_payload()` retornara el cuerpo codificado en lugar del texto legible. La solución fue usar `get_payload(decode=True)` que decodifica automáticamente.

**gnupg y passphrase incorrecta** — La biblioteca `python-gnupg` no lanza excepción consistentemente con passphrase incorrecta en todas las versiones instaladas. En algunas versiones retorna vacío silenciosamente. Los tests se adaptaron para aceptar ambos comportamientos.

**gnupg** — Hay ciertos errores con respecto a la passphrase necesaria para desencriptar los mensajes de gnupg que no se determinaron correctamente, por lo que fue descartado el modulo requerido.

**config.json vacío** — Al ejecutar `gui_main.py` por primera vez, `smtp/config.json` puede existir vacío causando `JSONDecodeError`. Se corrigió validando el contenido antes de parsear.

**Argumento `-h` en argparse** — `argparse` reserva `-h` para `--help`, por lo que el argumento del host del cliente SMTP se cambió a `-host` para evitar el conflicto.

### Limitaciones

- El dominio real no se configuró por limitaciones de tiempo con el proceso de verificación del GitHub Student Pack, su disponibilidad fue prevista para el 21 a las 23:30 aproximadamente.
- El módulo PGP quedó implementado y testeado pero se descartó de la integración principal por complejidad de la gestión de llaves en la GUI, por lo que fue finalmente eliminado del flujo del repositorio.
- El notificador XMPP requiere una cuenta XMPP existente (ej. `jabber.org`) para funcionar en producción.

### Reporte de commits

```bash
git log --oneline
```

```
260a930 (HEAD -> main, origin/main, origin/HEAD) Requerimientos + Binarios
a50c0bd Merge branch 'main' of https://github.com/IonDola/tarea_e-mail
ad24728 Funcionamiento de la interfaz, descarte del pgp.
2852421 Fix command option for smtpclient.py
d2f4c92 Merge pull request #5 from IonDola/dev
811214c (origin/dev) No continuable en windows.
64462f8 Implementación y pruebas de xmpp_notifier junto a ajustes menores
b3e6608 Merge pull request #4 from IonDola/dev
5c6c847 Pop3server funcional con sus test validos
d74ab9e Merge pull request #3 from IonDola/dev
7151f65 smtpclient funcionando correctamente con sus respectivos test implementados.
e0152d6 Merge pull request #2 from IonDola/dev
2cbb365 Test de smtpserver funcionales, errores encontrados en la misma bateria de pruebas
4e0b21f Modificacion al readme, implementación de Doxygen, implementación de smtpserver inicial con sus respectivos test (estado actual 4 errores, 25 aciertos y 19 alertas)
90f6e35 Merge pull request #1 from IonDola/dev
9f48a39 Esqueleto de la tarea producido
9043c56 Initial commit
```

### Calificación estimada por rúbrica

| Ítem | Valor | Autocalificación | Justificación |
|---|---|---|---|
| Kick-off | 5% adicional | 8/10 | Presentado a tiempo con esquema y ambiente |
| SMTP Server | 15% | 8/10 | Funcional con TLS, dominios y MIME |
| SMTP Client | 15% | 10/10 | CSV, plantillas, STARTTLS, servidores externos |
| POP3 Server | 15% | 10/10 | RFC 1939 completo con SSL |
| XMPP Notifier | 15% | 7/10 | Funcional pero requiere cuenta XMPP para probar en producción |
| SSL en POP3 y SMTP | 10% | 9/10 | Implementado y funcional |
| SMTP en dominio | 10% | 0/10 | No completado |
| Documentación | 20% | 8/10 | Completa en Markdown con todos los puntos del enunciado |
| GUI (opcional) | 5% | 8/10 | Funcional con todas las secciones requeridas |

---

## 11. Lecciones Aprendidas

Para estudiantes que cursen este proyecto en el futuro:

**Empezá por Twisted desde el día uno.** La curva de aprendizaje de Twisted es pronunciada. El modelo de programación asíncrona con `Deferred`, `Factory` y los protocolos `IMessage`/`IMessageDelivery` es muy diferente a lo que se trabaja en otros cursos. Leer la documentación oficial y los ejemplos de `twisted.mail` o simplemente preguntarle a una IA de confianza antes de escribir código ahorra días de frustración.

**El reactor de Twisted no se puede reiniciar.** Esto tiene implicaciones directas en las pruebas. Si levantás el reactor en un test, no podés levantarlo en otro test de la misma sesión de pytest. Usá siempre `scope="module"` o `scope="session"` en los fixtures que arrancan el reactor.

**Los mensajes MIME tienen muchas capas.** Lo que parece un texto plano puede estar codificado en base64, quoted-printable, o ser multipart. Siempre usá `get_payload(decode=True)` para obtener el contenido real, y `email.message_from_bytes()` para parsear correctamente en lugar de leer el `.eml` como texto crudo.

**Hacé el kick-off con detalle.** Vale 5% adicional si se presenta o menos 10% si no se presenta (creo), pero más importante, obliga a plantear la arquitectura antes de escribir código.

**Gestioná el dominio desde el primer día.** El proceso de verificación del GitHub Student Pack puede tardar días. Si esperás a la última semana para solicitar el dominio, es probable que no se llegue a tiempo para configurarlo, como ocurrió en este proyecto.

**Separar responsabilidades facilita el testing.** Clases como `MailboxMonitor`, `NotificationFormatter` y `UserAuth` son fáciles de testear en aislamiento porque no dependen de Twisted ni de conexiones de red. Siempre que puedas extraer lógica a una clase sin dependencias externas, hacelo.

---

## 12. Bibliografía

- Twisted Project. (2024). *Twisted Documentation*. https://docs.twisted.org/en/stable/
- Twisted Project. (2024). *twisted.mail API Reference*. https://docs.twisted.org/en/stable/api/twisted.mail.html
- Klensin, J. (2008). *RFC 5321 — Simple Mail Transfer Protocol*. IETF. https://tools.ietf.org/html/rfc5321
- Myers, J., & Rose, M. (1996). *RFC 1939 — Post Office Protocol Version 3*. IETF. https://tools.ietf.org/html/rfc1939
- Galvin, J., Murphy, S., Crocker, S., & Freed, N. (2001). *RFC 3156 — MIME Security with OpenPGP*. IETF. https://tools.ietf.org/html/rfc3156
- Saint-Andre, P. (2011). *RFC 6120 — Extensible Messaging and Presence Protocol (XMPP)*. IETF. https://tools.ietf.org/html/rfc6120
- Python Software Foundation. (2024). *email — Email and MIME handling package*. https://docs.python.org/3/library/email.html
- Python Software Foundation. (2024). *smtplib — SMTP protocol client*. https://docs.python.org/3/library/smtplib.html
- Caoimh, C. (2023). *python-gnupg documentation*. https://python-gnupg.readthedocs.io/
- Bottazzo, C. (2022). *Eel documentation*. https://github.com/python-eel/Eel
- OpenSSL Project. (2024). *OpenSSL documentation*. https://www.openssl.org/docs/
- Krekel, H., & pytest contributors. (2024). *pytest documentation*. https://docs.pytest.org/
- Van Heesch, D. (2024). *Doxygen — Generate documentation from source code*.
- Doxygen Project. https://www.doxygen.nl/index.html
- Mozilla Foundation. (2024). *Thunderbird — Free your inbox*.
  https://www.thunderbird.net/
- Bottazzo, C. (2024). *Eel — A little Python library for making simple 
  Electron-like HTML/JS GUI apps*. GitHub.
  https://github.com/python-eel/Eel
- Konopelchenko, M. (2020). *doxypypy — A Doxygen filter for Python*.
  GitHub. https://github.com/Feneric/doxypypy