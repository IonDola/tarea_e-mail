# test/smtpclient_test.py
"""
Pruebas unitarias para smtpclient.py
Uso: python -m pytest test/smtpclient_test.py -v
"""

import os
import sys
import pytest

from unittest.mock import MagicMock, patch
from email.mime.multipart import MIMEMultipart

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from smtp.smtpclient import (
    RecipientLoader,
    TemplateRenderer,
    MessageLoader,
    EmailBuilder,
    SMTPSender,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def csv_valido(tmp_path):
    f = tmp_path / "destinatarios.csv"
    f.write_text(
        "email,nombre,empresa\n"
        "alice@local.dev,Alice Mora,TEC\n"
        "bob@local.dev,Bob Solano,TEC\n",
        encoding="utf-8"
    )
    return str(f)


@pytest.fixture
def csv_columnas_extra(tmp_path):
    f = tmp_path / "extras.csv"
    f.write_text(
        "email,nombre,ciudad,carrera\n"
        "carlos@local.dev,Carlos,San José,Ingeniería\n",
        encoding="utf-8"
    )
    return str(f)


@pytest.fixture
def csv_sin_columnas_requeridas(tmp_path):
    f = tmp_path / "malo.csv"
    f.write_text("correo,apellido\nalice@local.dev,Mora\n", encoding="utf-8")
    return str(f)


@pytest.fixture
def csv_con_email_vacio(tmp_path):
    f = tmp_path / "vacios.csv"
    f.write_text(
        "email,nombre\n"
        "alice@local.dev,Alice\n"
        ",Sin Email\n"
        "bob@local.dev,Bob\n",
        encoding="utf-8"
    )
    return str(f)


@pytest.fixture
def mensaje_valido(tmp_path):
    f = tmp_path / "mensaje.txt"
    f.write_text(
        "Subject: Hola {{nombre}} de {{empresa}}\n"
        "\n"
        "Estimado {{nombre}},\n"
        "Tu correo es {{email}}.\n",
        encoding="utf-8"
    )
    return str(f)


@pytest.fixture
def mensaje_con_adjunto(tmp_path):
    adjunto = tmp_path / "doc.pdf"
    adjunto.write_bytes(b"%PDF-1.4 contenido falso")
    f = tmp_path / "mensaje_adj.txt"
    f.write_text(
        f"Subject: Con adjunto\n"
        f"Attachment: {str(adjunto)}\n"
        f"\n"
        f"Cuerpo con adjunto.\n",
        encoding="utf-8"
    )
    return str(f), str(adjunto)


@pytest.fixture
def mensaje_sin_subject(tmp_path):
    f = tmp_path / "sin_subject.txt"
    f.write_text("\nSolo cuerpo sin subject.\n", encoding="utf-8")
    return str(f)


@pytest.fixture
def recipient_data():
    return {
        "email":   "alice@local.dev",
        "nombre":  "Alice Mora",
        "empresa": "TEC",
    }


@pytest.fixture
def renderer():
    return TemplateRenderer()


@pytest.fixture
def builder():
    return EmailBuilder("sender@local.dev")


@pytest.fixture
def recipients():
    return [
        {"email": "alice@local.dev", "nombre": "Alice", "empresa": "TEC"},
        {"email": "bob@local.dev",   "nombre": "Bob",   "empresa": "TEC"},
    ]


@pytest.fixture
def template(mensaje_valido):
    return MessageLoader(mensaje_valido).load()


def mock_smtp():
    m = MagicMock()
    m.esmtp_features = {}
    return m


# ===========================================================================
# RecipientLoader
# ===========================================================================

class TestRecipientLoader:

    def test_carga_correctamente(self, csv_valido):
        result = RecipientLoader(csv_valido).load()
        assert len(result) == 2

    def test_claves_normalizadas_a_minusculas(self, csv_valido):
        result = RecipientLoader(csv_valido).load()
        for row in result:
            assert all(k == k.lower() for k in row.keys())

    def test_contiene_email_y_nombre(self, csv_valido):
        result = RecipientLoader(csv_valido).load()
        assert result[0]["email"]  == "alice@local.dev"
        assert result[0]["nombre"] == "Alice Mora"

    def test_columnas_extra_incluidas(self, csv_columnas_extra):
        result = RecipientLoader(csv_columnas_extra).load()
        assert "ciudad"  in result[0]
        assert "carrera" in result[0]

    def test_omite_filas_sin_email(self, csv_con_email_vacio):
        result = RecipientLoader(csv_con_email_vacio).load()
        assert len(result) == 2
        assert all(r["email"] for r in result)

    def test_error_si_csv_no_existe(self):
        with pytest.raises(FileNotFoundError):
            RecipientLoader("no_existe.csv").load()

    def test_error_si_faltan_columnas_requeridas(self, csv_sin_columnas_requeridas):
        with pytest.raises(ValueError, match="email"):
            RecipientLoader(csv_sin_columnas_requeridas).load()

    def test_valores_sin_espacios_extra(self, tmp_path):
        f = tmp_path / "espacios.csv"
        f.write_text("email,nombre\n  alice@local.dev  ,  Alice  \n", encoding="utf-8")
        result = RecipientLoader(str(f)).load()
        assert result[0]["email"]  == "alice@local.dev"
        assert result[0]["nombre"] == "Alice"


# ===========================================================================
# TemplateRenderer
# ===========================================================================

class TestTemplateRenderer:

    def test_sustituye_variable_simple(self, renderer):
        assert renderer.render("Hola {{nombre}}", {"nombre": "Alice"}) == "Hola Alice"

    def test_sustituye_multiples_variables(self, renderer):
        result = renderer.render(
            "{{nombre}} trabaja en {{empresa}}",
            {"nombre": "Alice", "empresa": "TEC"}
        )
        assert result == "Alice trabaja en TEC"

    def test_variable_no_encontrada_se_conserva(self, renderer):
        result = renderer.render("Hola {{desconocido}}", {"nombre": "Alice"})
        assert result == "Hola {{desconocido}}"

    def test_variable_repetida(self, renderer):
        result = renderer.render("{{nombre}} es {{nombre}}", {"nombre": "Alice"})
        assert result == "Alice es Alice"

    def test_sin_variables_retorna_igual(self, renderer):
        texto = "Sin variables aquí."
        assert renderer.render(texto, {"nombre": "Alice"}) == texto

    def test_case_insensitive_en_variables(self, renderer):
        result = renderer.render("Hola {{NOMBRE}}", {"nombre": "Alice"})
        assert result == "Hola Alice"

    def test_espacios_dentro_de_llaves(self, renderer):
        result = renderer.render("Hola {{ nombre }}", {"nombre": "Alice"})
        assert result == "Hola Alice"

    def test_template_vacio(self, renderer):
        assert renderer.render("", {"nombre": "Alice"}) == ""

    def test_dict_vacio(self, renderer):
        result = renderer.render("Hola {{nombre}}", {})
        assert result == "Hola {{nombre}}"


# ===========================================================================
# MessageLoader
# ===========================================================================

class TestMessageLoader:

    def test_carga_subject_y_body(self, mensaje_valido):
        result = MessageLoader(mensaje_valido).load()
        assert "{{nombre}}" in result["subject"]
        assert "{{email}}"  in result["body"]

    def test_subject_correcto(self, mensaje_valido):
        result = MessageLoader(mensaje_valido).load()
        assert result["subject"] == "Hola {{nombre}} de {{empresa}}"

    def test_body_correcto(self, mensaje_valido):
        result = MessageLoader(mensaje_valido).load()
        assert "Estimado {{nombre}}" in result["body"]

    def test_sin_subject_usa_default(self, mensaje_sin_subject):
        result = MessageLoader(mensaje_sin_subject).load()
        assert result["subject"] == "(sin asunto)"

    def test_attachment_none_si_no_se_especifica(self, mensaje_valido):
        result = MessageLoader(mensaje_valido).load()
        assert result["attachment"] is None

    def test_attachment_se_carga(self, mensaje_con_adjunto):
        path_msg, path_adj = mensaje_con_adjunto
        result = MessageLoader(path_msg).load()
        assert result["attachment"] == path_adj

    def test_error_si_archivo_no_existe(self):
        with pytest.raises(FileNotFoundError):
            MessageLoader("no_existe.txt").load()


# ===========================================================================
# EmailBuilder
# ===========================================================================

class TestEmailBuilder:

    def test_retorna_mime_multipart(self, builder, recipient_data, template):
        msg = builder.build(recipient_data, template)
        assert isinstance(msg, MIMEMultipart)

    def test_from_correcto(self, builder, recipient_data, template):
        msg = builder.build(recipient_data, template)
        assert msg["From"] == "sender@local.dev"

    def test_to_correcto(self, builder, recipient_data, template):
        msg = builder.build(recipient_data, template)
        assert msg["To"] == "alice@local.dev"

    def test_subject_con_variables_sustituidas(self, builder, recipient_data, template):
        msg = builder.build(recipient_data, template)
        assert "Alice Mora" in msg["Subject"]
        assert "TEC"        in msg["Subject"]

    def test_body_con_variables_sustituidas(self, builder, recipient_data, template):
        msg     = builder.build(recipient_data, template)
        payload = msg.get_payload()
        body    = payload[0].get_payload(decode=True).decode("utf-8")
        assert "Alice Mora"      in body
        assert "alice@local.dev" in body

    def test_sin_adjunto_tiene_una_parte(self, builder, recipient_data, template):
        msg = builder.build(recipient_data, template)
        assert len(msg.get_payload()) == 1

    def test_con_adjunto_tiene_dos_partes(self, builder, recipient_data, mensaje_con_adjunto):
        path_msg, _ = mensaje_con_adjunto
        template    = MessageLoader(path_msg).load()
        msg         = builder.build(recipient_data, template)
        assert len(msg.get_payload()) == 2

    def test_adjunto_no_existente_no_rompe(self, builder, recipient_data, tmp_path):
        f = tmp_path / "msg.txt"
        f.write_text(
            "Subject: Test\nAttachment: /no/existe/archivo.pdf\n\nCuerpo\n",
            encoding="utf-8"
        )
        template = MessageLoader(str(f)).load()
        msg      = builder.build(recipient_data, template)
        assert len(msg.get_payload()) == 1


# ===========================================================================
# SMTPSender
# ===========================================================================

class TestSMTPSender:

    def test_envia_a_todos_los_destinatarios(self, recipients, template):
        conn = mock_smtp()
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            summary = SMTPSender("localhost", 2525).send_all(
                "sender@local.dev", recipients, template
            )
        assert len(summary["ok"])     == 2
        assert len(summary["failed"]) == 0

    def test_ok_contiene_emails_correctos(self, recipients, template):
        conn = mock_smtp()
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            summary = SMTPSender("localhost", 2525).send_all(
                "sender@local.dev", recipients, template
            )
        assert "alice@local.dev" in summary["ok"]
        assert "bob@local.dev"   in summary["ok"]

    def test_fallo_en_un_destinatario_continua(self, recipients, template):
        conn = mock_smtp()
        conn.sendmail.side_effect = [Exception("Rechazo"), None]
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            summary = SMTPSender("localhost", 2525).send_all(
                "sender@local.dev", recipients, template
            )
        assert len(summary["ok"])     == 1
        assert len(summary["failed"]) == 1

    def test_failed_contiene_email_y_error(self, recipients, template):
        conn = mock_smtp()
        conn.sendmail.side_effect = Exception("Buzón lleno")
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            summary = SMTPSender("localhost", 2525).send_all(
                "sender@local.dev", recipients, template
            )
        assert summary["failed"][0]["email"] == "alice@local.dev"
        assert "Buzón lleno" in summary["failed"][0]["error"]

    def test_starttls_se_llama_cuando_use_tls_true(self, recipients, template):
        conn = mock_smtp()
        conn.esmtp_features = {"starttls": ""}
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            SMTPSender("localhost", 2525, use_tls=True).send_all(
                "sender@local.dev", recipients, template
            )
        conn.starttls.assert_called_once()

    def test_starttls_no_se_llama_cuando_use_tls_false(self, recipients, template):
        conn = mock_smtp()
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            SMTPSender("localhost", 2525, use_tls=False).send_all(
                "sender@local.dev", recipients, template
            )
        conn.starttls.assert_not_called()

    def test_login_se_llama_con_credenciales(self, recipients, template):
        conn = mock_smtp()
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            SMTPSender("localhost", 2525, username="user", password="pass").send_all(
                "sender@local.dev", recipients, template
            )
        conn.login.assert_called_once_with("user", "pass")

    def test_login_no_se_llama_sin_credenciales(self, recipients, template):
        conn = mock_smtp()
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            SMTPSender("localhost", 2525).send_all(
                "sender@local.dev", recipients, template
            )
        conn.login.assert_not_called()

    def test_quit_se_llama_al_terminar(self, recipients, template):
        conn = mock_smtp()
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            SMTPSender("localhost", 2525).send_all(
                "sender@local.dev", recipients, template
            )
        conn.quit.assert_called_once()

    def test_lista_vacia_retorna_summary_vacio(self, template):
        conn = mock_smtp()
        with patch("smtp.smtpclient.smtplib.SMTP", return_value=conn):
            summary = SMTPSender("localhost", 2525).send_all(
                "sender@local.dev", [], template
            )
        assert summary == {"ok": [], "failed": []}
        conn.sendmail.assert_not_called()