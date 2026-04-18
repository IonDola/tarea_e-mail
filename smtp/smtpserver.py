import argparse
from twisted.internet import protocol, reactor, ssl
from twisted.mail import smtp
from zope.interface import implementer
import os

# 1. Definición del mensaje (Maneja el almacenamiento y MIME)
@implementer(smtp.IMessage)
class MailMessage:
    def __init__(self, storage_path):
        self.storage_path = storage_path
        self.lines = []

    def lineReceived(self, line):
        self.lines.append(line)

    def eomReceived(self):
        # Almacenamiento simple en archivo (se puede mejorar a Maildir)
        print("Mensaje completo recibido. Guardando...")
        if not os.path.exists(self.storage_path):
            os.makedirs(self.storage_path)
        
        filename = os.path.join(self.storage_path, f"msg_{len(os.listdir(self.storage_path))}.eml")
        with open(filename, "wb") as f:
            f.write(b"\n".join(self.lines))
        
        self.lines = []
        return protocol.SuccessResponse("Correo guardado exitosamente")

    def connectionLost(self):
        print("Conexión perdida durante la recepción.")
        self.lines = []

# 2. Lógica de entrega y validación de dominios
@implementer(smtp.IMessageDelivery)
class LocalDelivery:
    def __init__(self, allowed_domains, storage_path):
        self.allowed_domains = allowed_domains
        self.storage_path = storage_path

    def receivedHeader(self, helo, origin, recipients):
        return b"Received: de nuestro servidor Twisted SMTP"

    def validateFrom(self, helo, origin):
        return origin

    def validateTo(self, user):
        # Requerimiento: Verificar dominios aceptados
        domain = user.dest.domain.decode()
        if domain in self.allowed_domains:
            print(f"Correo aceptado para el dominio: {domain}")
            return lambda: MailMessage(self.storage_path)
        print(f"Correo rechazado: dominio {domain} no permitido.")
        raise smtp.SMTPBadRcpt(user.dest)

class SMTPFactory(smtp.SMTPFactory):
    def __init__(self, domains, storage):
        self.domains = domains
        self.storage = storage

    def buildProtocol(self, addr):
        p = super().buildProtocol(addr)
        p.delivery = LocalDelivery(self.domains, self.storage)
        return p

# 3. Punto de entrada y manejo de argumentos
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Twisted SMTP Server")
    parser.add_argument("-d", "--domains", required=True, help="Dominios aceptados (separados por coma)")
    parser.add_argument("-s", "--mail-storage", required=True, help="Carpeta de almacenamiento")
    parser.add_argument("-p", "--port", type=int, required=True, help="Puerto de escucha")
    
    args = parser.parse_args()
    allowed_domains = args.domains.split(",")

    print(f"Servidor SMTP iniciado en puerto {args.port}...")
    print(f"Dominios permitidos: {allowed_domains}")

    factory = SMTPFactory(allowed_domains, args.mail_storage)
    reactor.listenTCP(args.port, factory)
    reactor.run()
