"""Flask approval workflow and license validation API."""

import hmac
import os
import smtplib
import sqlite3
from contextlib import contextmanager
from email.message import EmailMessage

from flask import Flask, Response, jsonify, render_template, request

from init_db import DATABASE, generate_license_key, init_db

# SMTP settings. Set these environment variables before approving requests.
SMTP_HOST = os.environ.get("OQ_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("OQ_SMTP_PORT", "465"))
SMTP_USER = os.environ.get("OQ_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("OQ_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("OQ_SMTP_FROM", SMTP_USER)

app = Flask(__name__)
init_db()


@contextmanager
def get_db():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def require_admin():
    auth = request.authorization
    username = auth.username if auth else ""
    password = auth.password if auth else ""
    expected = os.environ.get("OQ_ADMIN_PASSWORD", "OQsuperadmin2026")
    if not (hmac.compare_digest(username, "admin") and hmac.compare_digest(password, expected)):
        return Response("Требуется авторизация", 401, {"WWW-Authenticate": 'Basic realm="OQ Admin", charset="UTF-8"'})
    return None


def send_license_email(email, full_name, company_name, license_key):
    if not all((SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)):
        raise RuntimeError("SMTP не настроен: задайте OQ_SMTP_HOST, OQ_SMTP_USER, OQ_SMTP_PASSWORD и OQ_SMTP_FROM")
    message = EmailMessage()
    message["Subject"] = "Лицензионный ключ OQ Смета"
    message["From"] = SMTP_FROM
    message["To"] = email
    message.set_content(
        f"Здравствуйте, {full_name}!\n\n"
        f"Заявка компании «{company_name}» одобрена.\n"
        f"Ваш лицензионный ключ: {license_key}\n\n"
        "Введите ключ в приложении OQ Смета для активации."
    )
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(message)


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/register")
def register():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(status="error", message="Ожидается JSON-объект"), 400
    values = [data.get(field, "") for field in ("company_name", "full_name", "email", "phone")]
    if not all(isinstance(value, str) for value in values):
        return jsonify(status="error", message="Некорректные данные формы"), 400
    company_name, full_name, email, phone = (value.strip() for value in values)
    email = email.lower()
    if not company_name or not full_name or not email or "@" not in email or not email.split("@", 1)[1]:
        return jsonify(status="error", message="Заполните обязательные поля корректно"), 400
    if len(company_name) > 200 or len(full_name) > 200 or len(email) > 254 or len(phone) > 50:
        return jsonify(status="error", message="Слишком длинное значение поля"), 400
    try:
        with get_db() as connection:
            connection.execute(
                "INSERT INTO companies (company_name, full_name, email, phone, license_key, status) "
                "VALUES (?, ?, ?, ?, NULL, 'pending')",
                (company_name, full_name, email, phone),
            )
    except sqlite3.IntegrityError:
        return jsonify(status="error", message="Этот email уже зарегистрирован"), 409
    return jsonify(status="success"), 201


@app.get("/admin")
def admin():
    auth_error = require_admin()
    if auth_error:
        return auth_error
    with get_db() as connection:
        companies = connection.execute(
            "SELECT id, company_name, full_name, email, phone, license_key, status, created_at "
            "FROM companies ORDER BY created_at DESC, id DESC"
        ).fetchall()
        company_names = [row[0] for row in connection.execute(
            "SELECT DISTINCT company_name FROM companies ORDER BY company_name"
        ).fetchall()]
    response = Response(render_template("admin.html", companies=companies, company_names=company_names))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/admin/approve/<int:company_id>")
def approve(company_id):
    auth_error = require_admin()
    if auth_error:
        return auth_error
    try:
        with get_db() as connection:
            company = connection.execute(
                "SELECT email, full_name, company_name, status FROM companies WHERE id = ?",
                (company_id,),
            ).fetchone()
            if not company:
                return jsonify(status="error", message="Заявка не найдена"), 404
            if company["status"] != "pending":
                return jsonify(status="error", message="Заявка уже обработана"), 409
            key = generate_license_key()
            connection.execute(
                "UPDATE companies SET status = 'approved', license_key = ? WHERE id = ? AND status = 'pending'",
                (key, company_id),
            )
            send_license_email(company["email"], company["full_name"], company["company_name"], key)
    except (OSError, smtplib.SMTPException, RuntimeError) as error:
        app.logger.error("Не удалось отправить лицензию для заявки %s: %s", company_id, error)
        return jsonify(status="error", message="Письмо не отправлено; заявка осталась ожидающей"), 502
    except sqlite3.IntegrityError:
        return jsonify(status="error", message="Не удалось создать уникальный ключ"), 503
    return jsonify(status="success", license_key=key)


@app.post("/api/admin/reject/<int:company_id>")
def reject(company_id):
    auth_error = require_admin()
    if auth_error:
        return auth_error
    with get_db() as connection:
        cursor = connection.execute(
            "UPDATE companies SET status = 'rejected' WHERE id = ? AND status = 'pending'",
            (company_id,),
        )
        if cursor.rowcount == 0:
            exists = connection.execute("SELECT 1 FROM companies WHERE id = ?", (company_id,)).fetchone()
            if not exists:
                return jsonify(status="error", message="Заявка не найдена"), 404
            return jsonify(status="error", message="Заявка уже обработана"), 409
    return jsonify(status="success")


@app.post("/api/validate")
def validate():
    data = request.get_json(silent=True)
    key = data.get("license_key") if isinstance(data, dict) else None
    if not isinstance(key, str):
        return jsonify(status="error", message="Неверный ключ"), 400
    with get_db() as connection:
        company = connection.execute(
            "SELECT company_name FROM companies WHERE license_key = ? AND status = 'approved' AND is_active = 1",
            (key.strip().upper(),),
        ).fetchone()
    if company:
        return jsonify(status="success", company=company["company_name"])
    return jsonify(status="error", message="Неверный ключ")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
