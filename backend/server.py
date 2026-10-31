"""VaiProPolo API: account verification, registration, login, and ride offers.

This standard-library server is intended as a small deployable starter. Put it
behind HTTPS and a reverse proxy before exposing it to the public internet.
"""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", ROOT / "data" / "vaipropolo.sqlite3"))
UPLOAD_DIR = Path(os.environ.get("PRIVATE_UPLOAD_DIR", ROOT / "private_uploads"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
DESTINATION = "Polo Universitário de Macaé"
OTP_TTL_SECONDS = 10 * 60
RESEND_SECONDS = 120
OTP_MAX_ATTEMPTS = 5
EMAIL_LINK_TTL_SECONDS = 24 * 60 * 60
SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_REQUEST_BYTES = 70 * 1024 * 1024  # Fits a 50 MiB PDF encoded as Base64 plus JSON overhead.
MAX_CNH_BYTES = 50 * 1024 * 1024
REPORTING_TIMEZONE = ZoneInfo("America/Sao_Paulo")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")


class APIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def now_ts() -> int:
    return int(time.time())


def iso_from_ts(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def normalize_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise APIError(400, "Informe um e-mail válido.")
    return email


def normalize_phone(value: Any) -> str:
    phone = str(value or "").strip()
    if not PHONE_RE.fullmatch(phone):
        raise APIError(400, "Informe o telefone no formato internacional, por exemplo +5522999999999.")
    return phone


def valid_cpf(value: Any) -> bool:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    numbers = [int(char) for char in digits]
    first_sum = sum(number * weight for number, weight in zip(numbers[:9], range(10, 1, -1)))
    first_digit = 0 if first_sum % 11 < 2 else 11 - first_sum % 11
    second_sum = sum(number * weight for number, weight in zip(numbers[:10], range(11, 1, -1)))
    second_digit = 0 if second_sum % 11 < 2 else 11 - second_sum % 11
    return numbers[9:] == [first_digit, second_digit]


def verification_secret() -> bytes:
    secret = os.environ.get("VERIFICATION_HMAC_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise APIError(503, "O backend ainda não foi configurado com VERIFICATION_HMAC_SECRET.")
    return secret.encode("utf-8")


def digest(purpose: str, value: str) -> str:
    return hmac.new(verification_secret(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def db_connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def database():
    connection = db_connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with database() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS phone_challenges (
                phone TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                issued_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                resend_after INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                verified_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS email_challenges (
                email TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                issued_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                resend_after INTEGER NOT NULL,
                verified_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                role TEXT NOT NULL CHECK(role IN ('driver', 'passenger')),
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT NOT NULL UNIQUE,
                cpf TEXT NOT NULL UNIQUE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending_email', 'active')),
                car_make TEXT,
                car_model TEXT,
                plate TEXT,
                cnh_path TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_failures (
                email TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL DEFAULT 0,
                blocked_until INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS request_limits (
                limiter_key TEXT PRIMARY KEY,
                window_started INTEGER NOT NULL,
                count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS offers (
                id TEXT PRIMARY KEY,
                driver_id TEXT NOT NULL REFERENCES accounts(id),
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                departure_at TEXT NOT NULL,
                price_cents INTEGER NOT NULL CHECK(price_cents > 0),
                available_seats INTEGER NOT NULL CHECK(available_seats BETWEEN 1 AND 4),
                created_at INTEGER NOT NULL,
                total_seats INTEGER NOT NULL DEFAULT 1 CHECK(total_seats BETWEEN 1 AND 4),
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'cancelled'))
            );
            CREATE INDEX IF NOT EXISTS offers_departure_idx ON offers(departure_at);
            """
        )
        offer_columns = {row["name"] for row in db.execute("PRAGMA table_info(offers)")}
        if "total_seats" not in offer_columns:
            db.execute("ALTER TABLE offers ADD COLUMN total_seats INTEGER NOT NULL DEFAULT 1")
            db.execute("UPDATE offers SET total_seats=available_seats")
        if "status" not in offer_columns:
            db.execute("ALTER TABLE offers ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY,
                offer_id TEXT NOT NULL REFERENCES offers(id),
                passenger_id TEXT NOT NULL REFERENCES accounts(id),
                status TEXT NOT NULL CHECK(status IN ('confirmed','cancelled_by_passenger','cancelled_by_driver')),
                created_at INTEGER NOT NULL,
                cancelled_at INTEGER
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking_per_passenger_offer
                ON bookings(offer_id,passenger_id) WHERE status='confirmed';
            CREATE INDEX IF NOT EXISTS bookings_offer_status_idx ON bookings(offer_id,status);
            CREATE TABLE IF NOT EXISTS ratings (
                id TEXT PRIMARY KEY,
                booking_id TEXT NOT NULL REFERENCES bookings(id),
                reviewer_id TEXT NOT NULL REFERENCES accounts(id),
                target_id TEXT NOT NULL REFERENCES accounts(id),
                stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
                created_at INTEGER NOT NULL,
                UNIQUE(booking_id,reviewer_id),
                CHECK(reviewer_id != target_id)
            );
            CREATE INDEX IF NOT EXISTS ratings_target_time_idx ON ratings(target_id,created_at DESC);
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                booking_id TEXT NOT NULL REFERENCES bookings(id),
                kind TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                read_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS notifications_account_unread_idx ON notifications(account_id,read_at,created_at);
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                actor_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL,
                occurred_at INTEGER NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                details_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS audit_log_actor_time_idx ON audit_log(actor_id,occurred_at DESC);
            CREATE INDEX IF NOT EXISTS audit_log_event_time_idx ON audit_log(event_type,occurred_at DESC);
            """
        )


def rating_summary(db: sqlite3.Connection, target_id: str, target_role: str) -> dict[str, Any]:
    departure_cutoff = datetime.now(timezone.utc).isoformat()
    if target_role == "driver":
        recent_trips_sql = (
            "SELECT o.id AS trip_id FROM offers o WHERE o.driver_id=? AND o.departure_at<=? "
            "AND EXISTS (SELECT 1 FROM bookings b WHERE b.offer_id=o.id AND b.status='confirmed') "
            "ORDER BY o.departure_at DESC LIMIT 30"
        )
    else:
        recent_trips_sql = (
            "SELECT b.id AS trip_id FROM bookings b JOIN offers o ON o.id=b.offer_id "
            "WHERE b.passenger_id=? AND b.status='confirmed' AND o.departure_at<=? "
            "ORDER BY o.departure_at DESC LIMIT 30"
        )
    if target_role == "driver":
        join_sql = (
            "LEFT JOIN bookings b ON b.offer_id=t.trip_id AND b.status='confirmed' "
            "LEFT JOIN ratings r ON r.booking_id=b.id AND r.target_id=?"
        )
    else:
        join_sql = (
            "LEFT JOIN ratings r ON r.booking_id=t.trip_id AND r.target_id=?"
        )
    row = db.execute(
        "WITH recent_trips AS (" + recent_trips_sql + "), "
        "trip_scores AS (SELECT t.trip_id,AVG(r.stars) AS trip_average,COUNT(r.id) AS review_count "
        "FROM recent_trips t " + join_sql + " GROUP BY t.trip_id) "
        "SELECT AVG(trip_average) AS average,COALESCE(SUM(review_count),0) AS review_count, "
        "COUNT(trip_average) AS rated_trips,COUNT(*) AS completed_trips_sampled FROM trip_scores",
        (target_id, departure_cutoff, target_id),
    ).fetchone()
    average = float(row["average"]) if row["average"] is not None else None
    return {
        "stars_average": round(average, 1) if average is not None else None,
        "stars_average_exact": round(average, 3) if average is not None else None,
        "review_count": row["review_count"],
        "rated_trips": row["rated_trips"],
        "completed_trips_sampled": row["completed_trips_sampled"],
        "window": 30,
        "anonymous": True,
    }


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


def smtp_send(recipient: str, subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST")
    sender = os.environ.get("SMTP_FROM")
    if not host or not sender:
        raise APIError(503, "O envio de e-mail ainda não está configurado no servidor.")
    port = int(os.environ.get("SMTP_PORT", "587"))
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    username = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    try:
        if os.environ.get("SMTP_USE_SSL", "false").lower() == "true":
            with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as server:
                if username:
                    server.login(username, password or "")
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                if username:
                    server.login(username, password or "")
                server.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        raise APIError(502, "O provedor de e-mail não conseguiu enviar a mensagem.") from error


def send_phone_message(phone: str, channel: str, code: str) -> None:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    sms_from = os.environ.get("TWILIO_SMS_FROM")
    whatsapp_from = os.environ.get("TWILIO_WHATSAPP_FROM")
    whatsapp_content_sid = os.environ.get("TWILIO_WHATSAPP_CONTENT_SID")
    sender = whatsapp_from if channel == "whatsapp" else sms_from
    if not sid or not token or not sender:
        raise APIError(503, f"O envio por {channel} ainda não está configurado no servidor.")
    if channel == "whatsapp" and not whatsapp_content_sid:
        raise APIError(503, "Configure um template de WhatsApp aprovado em TWILIO_WHATSAPP_CONTENT_SID.")
    recipient = f"whatsapp:{phone}" if channel == "whatsapp" else phone
    sender = sender if sender.startswith("whatsapp:") or channel != "whatsapp" else f"whatsapp:{sender}"
    message_fields = {"To": recipient, "From": sender}
    if channel == "whatsapp":
        message_fields["ContentSid"] = whatsapp_content_sid
        message_fields["ContentVariables"] = json.dumps({"1": code})
    else:
        message_fields["Body"] = f"Seu código VaiProPolo é {code}. Ele expira em 10 minutos."
    fields = urllib.parse.urlencode(message_fields).encode()
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=fields,
        headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status >= 300:
                raise APIError(502, "O provedor não aceitou o envio do código.")
    except (OSError, urllib.error.URLError) as error:
        raise APIError(502, "Não foi possível enviar o código pelo provedor.") from error


def verification_email_body(name: str, link: str) -> str:
    return (
        f"Olá, {name}.\n\n"
        "Recebemos uma solicitação para criar sua conta. Para confirmar seu e-mail e desbloquear o acesso ao aplicativo, acesse:\n\n"
        f"Desbloquear minha conta: {link}\n\n"
        "Este link é pessoal, válido por 24 horas e pode ser usado uma única vez. "
        "Se você não solicitou a criação da conta, ignore esta mensagem.\n\n"
        "Por segurança, não compartilhe este link com ninguém.\n\n"
        "Equipe VaiProPolo"
    )


def send_email_challenge(email: str, name: str) -> None:
    current = now_ts()
    raw_token = secrets.token_urlsafe(32)
    token_hash = digest("email", raw_token)
    with database() as db:
        row = db.execute("SELECT resend_after FROM email_challenges WHERE email = ?", (email,)).fetchone()
        if row and row["resend_after"] > current:
            raise APIError(429, f"Aguarde {row['resend_after'] - current} segundos para reenviar o e-mail.")
        db.execute(
            "INSERT INTO email_challenges(email,name,token_hash,issued_at,expires_at,resend_after,verified_at) "
            "VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(email) DO UPDATE SET name=excluded.name, token_hash=excluded.token_hash, "
            "issued_at=excluded.issued_at, expires_at=excluded.expires_at, resend_after=excluded.resend_after, verified_at=NULL",
            (email, name, token_hash, current, current + EMAIL_LINK_TTL_SECONDS, current + RESEND_SECONDS),
        )
    # Put the secret in the fragment so it is not sent to or recorded by web servers/proxies in the GET URL.
    link = f"{PUBLIC_BASE_URL}/verify-email#token={urllib.parse.quote(raw_token)}"
    try:
        smtp_send(email, "Confirme seu e-mail para desbloquear sua conta", verification_email_body(name, link))
    except APIError:
        # Keep the resend cooldown even if the mail provider fails.
        raise


def save_cnh(account_id: str, encoded: Any) -> str:
    if not isinstance(encoded, str):
        raise APIError(400, "Envie o PDF da CNH como cnh_pdf_base64.")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise APIError(400, "O PDF da CNH não está em Base64 válido.") from error
    if len(payload) > MAX_CNH_BYTES:
        raise APIError(413, "O PDF da CNH deve ter até 50 MB.")
    if not payload.startswith(b"%PDF-"):
        raise APIError(400, "O arquivo enviado não é um PDF válido.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{account_id}-{secrets.token_hex(12)}.pdf"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(payload)
    return str(path)


def account_from_token(header: str | None) -> sqlite3.Row:
    if not header or not header.startswith("Bearer "):
        raise APIError(401, "Autenticação necessária.")
    token = header[7:].strip()
    if not token or len(token) > 128:
        raise APIError(401, "Token de acesso inválido.")
    with database() as db:
        row = db.execute(
            "SELECT a.* FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=? AND s.expires_at>?",
            (digest("session", token), now_ts()),
        ).fetchone()
    if not row:
        raise APIError(401, "Sessão inválida ou expirada.")
    if row["status"] != "active":
        raise APIError(403, "Conta bloqueada. Confirme o e-mail para desbloquear o acesso.")
    return row


class Handler(BaseHTTPRequestHandler):
    server_version = "VaiProPoloAPI/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        # Avoid BaseHTTPRequestHandler's default log of raw paths/query strings.
        print(f"{self.client_address[0]} {self.command} {urllib.parse.urlparse(self.path).path}")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin")
        allowed_origin = os.environ.get("CORS_ORIGIN", "*")
        if allowed_origin == "*" or origin == allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise APIError(400, "Content-Length inválido.") from error
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise APIError(413 if length > MAX_REQUEST_BYTES else 400, "Tamanho da solicitação inválido.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise APIError(400, "Corpo JSON inválido.") from error
        if not isinstance(payload, dict):
            raise APIError(400, "O corpo deve ser um objeto JSON.")
        return payload

    def _check_ip_limit(self, action: str, limit: int, window_seconds: int) -> None:
        current = now_ts()
        limiter_key = f"{action}:{self.client_address[0]}"
        exceeded = False
        with database() as db:
            row = db.execute("SELECT window_started,count FROM request_limits WHERE limiter_key=?", (limiter_key,)).fetchone()
            if not row or current - row["window_started"] >= window_seconds:
                db.execute(
                    "INSERT INTO request_limits(limiter_key,window_started,count) VALUES(?,?,1) "
                    "ON CONFLICT(limiter_key) DO UPDATE SET window_started=excluded.window_started,count=1",
                    (limiter_key, current),
                )
            elif row["count"] >= limit:
                exceeded = True
            else:
                db.execute("UPDATE request_limits SET count=count+1 WHERE limiter_key=?", (limiter_key,))
        if exceeded:
            raise APIError(429, "Limite de solicitações atingido. Aguarde antes de tentar novamente.")

    def _run(self, action: Any) -> None:
        try:
            action()
        except APIError as error:
            self._send_json(error.status, {"error": error.message})
        except sqlite3.IntegrityError:
            self._send_json(409, {"error": "Já existe uma conta com este e-mail, telefone ou CPF."})
        except Exception:
            # Do not return internal exception text or secrets to clients.
            self._send_json(500, {"error": "Erro interno do servidor."})

    def _write_audit(self, db: sqlite3.Connection, event_type: str, actor_id: str | None,
                     details: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO audit_log(id,actor_id,event_type,occurred_at,ip_address,user_agent,details_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), actor_id, event_type, now_ts(), self.client_address[0],
             self.headers.get("User-Agent", "")[:500], json.dumps(details, ensure_ascii=False, sort_keys=True)),
        )

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", os.environ.get("CORS_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/v1/health":
            return self._run(lambda: self._send_json(200, {"status": "ok"}))
        if parsed.path == "/v1/offers":
            return self._run(self._list_offers)
        if parsed.path == "/v1/me/offers":
            return self._run(self._list_my_offers)
        if parsed.path == "/v1/me/bookings":
            return self._run(self._list_my_bookings)
        if parsed.path == "/v1/me/trips":
            return self._run(self._list_my_trips)
        if parsed.path == "/v1/me/notifications":
            return self._run(self._list_my_notifications)
        if parsed.path == "/v1/me/activity":
            return self._run(self._list_my_activity)
        if parsed.path == "/v1/admin/dashboard":
            return self._run(self._admin_dashboard)
        if parsed.path == "/v1/me/rating":
            return self._run(self._my_rating)
        rating_match = re.fullmatch(r"/v1/profiles/([0-9a-fA-F-]{36})/rating", parsed.path)
        if rating_match:
            return self._run(lambda: self._profile_rating(rating_match.group(1)))
        if parsed.path == "/admin":
            return self._run(self._admin_dashboard_page)
        if parsed.path == "/verify-email":
            return self._run(self._verification_landing_page)
        self._send_json(404, {"error": "Rota não encontrada."})

    def do_POST(self) -> None:
        routes = {
            "/v1/verification/phone/request": self._request_phone_code,
            "/v1/verification/phone/confirm": self._confirm_phone_code,
            "/v1/verification/email/request": self._request_email_link,
            "/v1/verification/email/confirm": self._confirm_email_link,
            "/v1/accounts/register": self._register_account,
            "/v1/auth/login": self._login,
            "/v1/auth/logout": self._logout,
            "/v1/offers": self._create_offer,
        }
        request_path = urllib.parse.urlparse(self.path).path
        action = routes.get(request_path)
        booking_match = re.fullmatch(r"/v1/offers/([0-9a-fA-F-]{36})/book", request_path)
        if booking_match:
            action = lambda: self._book_offer(booking_match.group(1))
        notification_match = re.fullmatch(r"/v1/notifications/([0-9a-fA-F-]{36})/read", request_path)
        if notification_match:
            action = lambda: self._mark_notification_read(notification_match.group(1))
        rating_match = re.fullmatch(r"/v1/bookings/([0-9a-fA-F-]{36})/ratings", request_path)
        if rating_match:
            action = lambda: self._submit_rating(rating_match.group(1))
        if not action:
            return self._send_json(404, {"error": "Rota não encontrada."})
        self._run(action)

    def do_PATCH(self) -> None:
        request_path = urllib.parse.urlparse(self.path).path
        offer_match = re.fullmatch(r"/v1/offers/([0-9a-fA-F-]{36})", request_path)
        if not offer_match:
            return self._send_json(404, {"error": "Rota não encontrada."})
        self._run(lambda: self._update_offer(offer_match.group(1)))

    def do_DELETE(self) -> None:
        request_path = urllib.parse.urlparse(self.path).path
        offer_match = re.fullmatch(r"/v1/offers/([0-9a-fA-F-]{36})", request_path)
        if offer_match:
            return self._run(lambda: self._cancel_offer(offer_match.group(1)))
        booking_match = re.fullmatch(r"/v1/bookings/([0-9a-fA-F-]{36})", request_path)
        if booking_match:
            return self._run(lambda: self._cancel_booking(booking_match.group(1)))
        self._send_json(404, {"error": "Rota não encontrada."})

    def _request_phone_code(self) -> None:
        self._check_ip_limit("phone-code", 8, 600)
        data = self._json_body()
        phone = normalize_phone(data.get("phone"))
        channel = str(data.get("channel", "")).lower()
        if channel not in ("sms", "whatsapp"):
            raise APIError(400, "Canal deve ser sms ou whatsapp.")
        current = now_ts()
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = digest("phone", f"{phone}:{code}")
        with database() as db:
            row = db.execute("SELECT resend_after FROM phone_challenges WHERE phone=?", (phone,)).fetchone()
            if row and row["resend_after"] > current:
                raise APIError(429, f"Aguarde {row['resend_after'] - current} segundos para pedir outro código.")
            db.execute(
                "INSERT INTO phone_challenges(phone,channel,code_hash,issued_at,expires_at,resend_after,attempts,verified_at) "
                "VALUES(?,?,?,?,?,?,0,NULL) ON CONFLICT(phone) DO UPDATE SET channel=excluded.channel, code_hash=excluded.code_hash, "
                "issued_at=excluded.issued_at, expires_at=excluded.expires_at, resend_after=excluded.resend_after, attempts=0, verified_at=NULL",
                (phone, channel, code_hash, current, current + OTP_TTL_SECONDS, current + RESEND_SECONDS),
            )
        # Keep the cooldown even if the provider fails; this prevents abuse.
        send_phone_message(phone, channel, code)
        self._send_json(202, {"sent": True, "expires_in_seconds": OTP_TTL_SECONDS, "resend_after_seconds": RESEND_SECONDS})

    def _confirm_phone_code(self) -> None:
        data = self._json_body()
        phone = normalize_phone(data.get("phone"))
        code = str(data.get("code", ""))
        if not re.fullmatch(r"[0-9]{6}", code):
            raise APIError(400, "O código deve conter exatamente 6 dígitos.")
        current = now_ts()
        incorrect_code = False
        with database() as db:
            row = db.execute("SELECT * FROM phone_challenges WHERE phone=?", (phone,)).fetchone()
            if not row or row["expires_at"] < current:
                raise APIError(400, "Código ausente ou expirado. Solicite outro.")
            if row["attempts"] >= OTP_MAX_ATTEMPTS:
                raise APIError(429, "Limite de tentativas atingido. Solicite outro código.")
            expected = digest("phone", f"{phone}:{code}")
            if not hmac.compare_digest(row["code_hash"], expected):
                db.execute("UPDATE phone_challenges SET attempts=attempts+1 WHERE phone=?", (phone,))
                incorrect_code = True
            else:
                db.execute("UPDATE phone_challenges SET verified_at=? WHERE phone=?", (current, phone))
        if incorrect_code:
            raise APIError(400, "Código incorreto.")
        self._send_json(200, {"verified": True})

    def _request_email_link(self) -> None:
        self._check_ip_limit("email-link", 8, 600)
        data = self._json_body()
        email = normalize_email(data.get("email"))
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 160:
            raise APIError(400, "Informe o nome para personalizar o e-mail.")
        if not PUBLIC_BASE_URL.startswith("https://") and "localhost" not in PUBLIC_BASE_URL:
            raise APIError(503, "PUBLIC_BASE_URL deve usar HTTPS.")
        send_email_challenge(email, name)
        self._send_json(202, {"sent": True, "expires_in_seconds": EMAIL_LINK_TTL_SECONDS, "resend_after_seconds": RESEND_SECONDS})

    def _confirm_email_link(self) -> None:
        data = self._json_body()
        token = str(data.get("token", ""))
        if not 32 <= len(token) <= 128:
            raise APIError(400, "Link de confirmação inválido.")
        token_hash = digest("email", token)
        current = now_ts()
        with database() as db:
            row = db.execute("SELECT email,expires_at,verified_at FROM email_challenges WHERE token_hash=?", (token_hash,)).fetchone()
            if not row or row["expires_at"] <= current or row["verified_at"] is not None:
                raise APIError(400, "Link inválido, expirado ou já utilizado.")
            db.execute("UPDATE email_challenges SET verified_at=?, token_hash='' WHERE email=?", (current, row["email"]))
            db.execute("UPDATE accounts SET status='active' WHERE email=? AND status='pending_email'", (row["email"],))
        self._send_json(200, {"verified": True})

    def _register_account(self) -> None:
        data = self._json_body()
        role = str(data.get("role", ""))
        name = str(data.get("full_name", "")).strip()
        email = normalize_email(data.get("email"))
        phone = normalize_phone(data.get("phone"))
        cpf = re.sub(r"\D", "", str(data.get("cpf", "")))
        password = str(data.get("password", ""))
        if role not in ("driver", "passenger"):
            raise APIError(400, "Tipo de conta deve ser driver ou passenger.")
        if not name or len(name) > 160:
            raise APIError(400, "Informe o nome completo.")
        if not valid_cpf(cpf):
            raise APIError(400, "CPF inválido.")
        if len(password) < 12 or len(password) > 128:
            raise APIError(400, "A senha deve ter entre 12 e 128 caracteres.")
        current = now_ts()
        with database() as db:
            phone_row = db.execute("SELECT verified_at FROM phone_challenges WHERE phone=?", (phone,)).fetchone()
            if not phone_row or phone_row["verified_at"] is None:
                raise APIError(403, "Confirme o telefone antes de cadastrar a conta.")
            email_row = db.execute("SELECT verified_at FROM email_challenges WHERE email=?", (email,)).fetchone()
            email_verified = bool(email_row and email_row["verified_at"] is not None)

        account_id = str(uuid.uuid4())
        salt = secrets.token_bytes(16)
        cnh_path = None
        car_make = car_model = plate = None
        if role == "driver":
            car_make = str(data.get("car_make", "")).strip()
            car_model = str(data.get("car_model", "")).strip()
            plate = re.sub(r"[^A-Z0-9]", "", str(data.get("plate", "")).upper())
            if not car_make or not car_model or len(plate) != 7:
                raise APIError(400, "Motorista deve informar marca, modelo e placa completa de 7 caracteres.")
            cnh_path = save_cnh(account_id, data.get("cnh_pdf_base64"))

        try:
            with database() as db:
                db.execute(
                    "INSERT INTO accounts(id,role,full_name,email,phone,cpf,password_salt,password_hash,status,car_make,car_model,plate,cnh_path,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (account_id, role, name, email, phone, cpf, salt, password_hash(password, salt),
                     "active" if email_verified else "pending_email", car_make, car_model, plate, cnh_path, current),
                )
                self._write_audit(db, "account.created", account_id, {
                    "role": role, "account_status": "active" if email_verified else "pending_email",
                })
        except sqlite3.IntegrityError:
            if cnh_path:
                Path(cnh_path).unlink(missing_ok=True)
            raise
        if not email_verified:
            try:
                send_email_challenge(email, name)
            except APIError:
                # The account stays blocked until the email provider is configured and a link is sent.
                pass
        self._send_json(201, {"account_id": account_id, "status": "active" if email_verified else "pending_email"})

    def _login(self) -> None:
        self._check_ip_limit("login", 20, 600)
        data = self._json_body()
        email = normalize_email(data.get("email"))
        password = str(data.get("password", ""))
        if len(password) > 128:
            raise APIError(401, "E-mail ou senha incorretos.")
        blocked = False
        invalid_credentials = False
        pending_email = False
        with database() as db:
            failure = db.execute("SELECT attempts,blocked_until FROM login_failures WHERE email=?", (email,)).fetchone()
            if failure and failure["blocked_until"] > now_ts():
                blocked = True
            account = db.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            if blocked:
                self._write_audit(db, "auth.login_rate_limited", account["id"] if account else None,
                                  {"reason": "temporary_lockout"})
            else:
                salt = account["password_salt"] if account else bytes(16)
                expected_hash = account["password_hash"] if account else bytes(32)
                candidate = password_hash(password, salt)
                if not account or not hmac.compare_digest(expected_hash, candidate):
                    attempts = (failure["attempts"] if failure else 0) + 1
                    blocked_until = now_ts() + 900 if attempts >= 5 else 0
                    db.execute(
                        "INSERT INTO login_failures(email,attempts,blocked_until) VALUES(?,?,?) "
                        "ON CONFLICT(email) DO UPDATE SET attempts=excluded.attempts,blocked_until=excluded.blocked_until",
                        (email, attempts, blocked_until),
                    )
                    self._write_audit(db, "auth.login_failed", account["id"] if account else None,
                                      {"reason": "invalid_credentials"})
                    invalid_credentials = True
                elif account["status"] != "active":
                    self._write_audit(db, "auth.login_blocked", account["id"], {"reason": "pending_email"})
                    pending_email = True
                else:
                    db.execute("DELETE FROM login_failures WHERE email=?", (email,))
                    token = secrets.token_urlsafe(32)
                    db.execute("INSERT INTO sessions(token_hash,account_id,expires_at) VALUES(?,?,?)",
                               (digest("session", token), account["id"], now_ts() + SESSION_TTL_SECONDS))
                    self._write_audit(db, "auth.login_succeeded", account["id"],
                                      {"session_expires_at": now_ts() + SESSION_TTL_SECONDS})
        if blocked:
            raise APIError(429, "Muitas tentativas de acesso. Tente novamente em 15 minutos.")
        if invalid_credentials:
            raise APIError(401, "E-mail ou senha incorretos.")
        if pending_email:
            raise APIError(403, "Conta bloqueada. Confirme o e-mail pelo link enviado para desbloquear.")
        self._send_json(200, {"access_token": token, "token_type": "Bearer", "expires_in_seconds": SESSION_TTL_SECONDS,
                              "account": {"id": account["id"], "role": account["role"], "full_name": account["full_name"]}})

    def _logout(self) -> None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise APIError(401, "Autenticação necessária.")
        token = header[7:].strip()
        if not token or len(token) > 128:
            raise APIError(401, "Token de acesso inválido.")
        with database() as db:
            row = db.execute("SELECT account_id FROM sessions WHERE token_hash=?", (digest("session", token),)).fetchone()
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest("session", token),))
            if row:
                self._write_audit(db, "auth.logout", row["account_id"], {})
        self._send_json(200, {"logged_out": True})

    def _create_offer(self) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        if account["role"] != "driver":
            raise APIError(403, "Somente motoristas podem publicar ofertas.")
        data = self._json_body()
        origin = str(data.get("origin", "")).strip()
        if not origin or len(origin) > 200:
            raise APIError(400, "Informe o ponto de partida.")
        departure = str(data.get("departure_at", ""))
        try:
            departure_dt = datetime.fromisoformat(departure.replace("Z", "+00:00"))
        except ValueError as error:
            raise APIError(400, "departure_at deve ser uma data ISO-8601.") from error
        if departure_dt.tzinfo is None or departure_dt <= datetime.now(timezone.utc):
            raise APIError(400, "Informe uma data futura com fuso horário.")
        try:
            price_value = Decimal(str(data.get("price")))
            seats = int(data.get("available_seats"))
        except (TypeError, ValueError, InvalidOperation) as error:
            raise APIError(400, "Valor e quantidade de vagas inválidos.") from error
        if isinstance(data.get("available_seats"), bool) or not price_value.is_finite() or price_value <= 0 or price_value > Decimal("999999.99") or seats not in range(1, 5):
            raise APIError(400, "Valor deve ser maior que zero e as vagas devem estar entre 1 e 4.")
        price_cents = int((price_value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        offer_id = str(uuid.uuid4())
        with database() as db:
            db.execute(
                "INSERT INTO offers(id,driver_id,origin,destination,departure_at,price_cents,available_seats,created_at,total_seats,status) "
                "VALUES(?,?,?,?,?,?,?,?,?,'active')",
                (offer_id, account["id"], origin, DESTINATION, departure_dt.astimezone(timezone.utc).isoformat(),
                 price_cents, seats, now_ts(), seats),
            )
            self._write_audit(db, "offer.created", account["id"], {
                "offer_id": offer_id, "origin": origin, "destination": DESTINATION,
                "departure_at": departure_dt.astimezone(timezone.utc).isoformat(),
                "price_cents": price_cents, "currency": "BRL", "total_seats": seats,
            })
        self._send_json(201, {"offer_id": offer_id, "destination": DESTINATION})

    def _list_offers(self) -> None:
        account_from_token(self.headers.get("Authorization"))
        current = datetime.now(timezone.utc).isoformat()
        with database() as db:
            rows = db.execute(
                "SELECT o.*,a.full_name,a.car_make,a.car_model,a.plate, "
                "o.total_seats-(SELECT COUNT(*) FROM bookings b WHERE b.offer_id=o.id AND b.status='confirmed') AS seats_left "
                "FROM offers o JOIN accounts a ON a.id=o.driver_id "
                "WHERE a.status='active' AND o.status='active' AND o.departure_at>? AND seats_left>0 ORDER BY o.departure_at", (current,)
            ).fetchall()
            offers = [{"id": row["id"], "driver_id": row["driver_id"], "driver_name": row["full_name"],
                       "origin": row["origin"], "destination": row["destination"],
                       "departure_at": row["departure_at"], "price": row["price_cents"] / 100,
                       "available_seats": row["seats_left"], "car_make": row["car_make"],
                       "car_model": row["car_model"], "plate_suffix": row["plate"][-4:],
                       "driver_rating": rating_summary(db, row["driver_id"], "driver")} for row in rows]
        self._send_json(200, {"offers": offers})

    def _list_my_offers(self) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        if account["role"] != "driver":
            raise APIError(403, "Somente motoristas têm ofertas para gerenciar.")
        with database() as db:
            rows = db.execute(
                "SELECT o.*,o.total_seats-(SELECT COUNT(*) FROM bookings b WHERE b.offer_id=o.id AND b.status='confirmed') AS seats_left "
                "FROM offers o WHERE o.driver_id=? ORDER BY o.created_at DESC", (account["id"],)
            ).fetchall()
        self._send_json(200, {"offers": [{
            "id": row["id"], "origin": row["origin"], "destination": row["destination"],
            "departure_at": row["departure_at"], "price": row["price_cents"] / 100,
            "available_seats": row["seats_left"], "total_seats": row["total_seats"], "status": row["status"],
        } for row in rows]})

    def _list_my_bookings(self) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        if account["role"] != "passenger":
            raise APIError(403, "Somente passageiros têm reservas para gerenciar.")
        with database() as db:
            rows = db.execute(
                "SELECT b.id AS booking_id,b.status AS booking_status,o.id AS offer_id,o.origin,o.destination, "
                "o.departure_at,o.price_cents,o.status AS offer_status,a.id AS driver_id,a.full_name AS driver_name,a.car_make,a.car_model,a.plate "
                "FROM bookings b JOIN offers o ON o.id=b.offer_id JOIN accounts a ON a.id=o.driver_id "
                "WHERE b.passenger_id=? ORDER BY b.created_at DESC", (account["id"],)
            ).fetchall()
            bookings = [{
                "booking_id": row["booking_id"], "status": row["booking_status"], "offer_id": row["offer_id"],
                "origin": row["origin"], "destination": row["destination"], "departure_at": row["departure_at"],
                "price": row["price_cents"] / 100, "driver_id": row["driver_id"],
                "driver_name": row["driver_name"], "car_make": row["car_make"], "car_model": row["car_model"],
                "plate_suffix": row["plate"][-4:],
                "can_rate": row["booking_status"] == "confirmed" and row["offer_status"] == "active"
                            and datetime.fromisoformat(row["departure_at"]) <= datetime.now(timezone.utc),
                "has_rated": db.execute("SELECT 1 FROM ratings WHERE booking_id=? AND reviewer_id=?",
                                         (row["booking_id"], account["id"])).fetchone() is not None,
                "driver_rating": rating_summary(db, row["driver_id"], "driver"),
            } for row in rows]
        self._send_json(200, {"bookings": bookings})

    def _list_my_trips(self) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        if account["role"] != "driver":
            raise APIError(403, "Somente motoristas podem consultar as viagens da prÃ³pria oferta.")
        with database() as db:
            rows = db.execute(
                "SELECT b.id AS booking_id,b.status AS booking_status,b.passenger_id,p.full_name AS passenger_name, "
                "o.id AS offer_id,o.origin,o.destination,o.departure_at,o.price_cents,o.status AS offer_status "
                "FROM bookings b JOIN offers o ON o.id=b.offer_id JOIN accounts p ON p.id=b.passenger_id "
                "WHERE o.driver_id=? ORDER BY o.departure_at DESC,b.created_at", (account["id"],)
            ).fetchall()
            trips = [{
                "booking_id": row["booking_id"], "status": row["booking_status"],
                "passenger_id": row["passenger_id"], "passenger_name": row["passenger_name"],
                "offer_id": row["offer_id"], "origin": row["origin"], "destination": row["destination"],
                "departure_at": row["departure_at"], "price": row["price_cents"] / 100,
                "can_rate": row["booking_status"] == "confirmed" and row["offer_status"] == "active"
                            and datetime.fromisoformat(row["departure_at"]) <= datetime.now(timezone.utc),
                "has_rated": db.execute("SELECT 1 FROM ratings WHERE booking_id=? AND reviewer_id=?",
                                         (row["booking_id"], account["id"])).fetchone() is not None,
                "passenger_rating": rating_summary(db, row["passenger_id"], "passenger"),
            } for row in rows]
        self._send_json(200, {"trips": trips})

    def _list_my_notifications(self) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        if account["role"] != "passenger":
            raise APIError(403, "Somente passageiros podem consultar notificações de cancelamento.")
        with database() as db:
            rows = db.execute(
                "SELECT id,booking_id,kind,message,created_at FROM notifications "
                "WHERE account_id=? AND read_at IS NULL ORDER BY created_at DESC",
                (account["id"],),
            ).fetchall()
        self._send_json(200, {"notifications": [{
            "id": row["id"], "booking_id": row["booking_id"], "kind": row["kind"],
            "message": row["message"], "created_at": iso_from_ts(row["created_at"]),
            "presentation": "popup",
        } for row in rows]})

    def _list_my_activity(self) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        with database() as db:
            rows = db.execute(
                "SELECT event_type,occurred_at,details_json FROM audit_log "
                "WHERE actor_id=? ORDER BY occurred_at DESC LIMIT 200", (account["id"],)
            ).fetchall()
        self._send_json(200, {"activity": [{
            "event": row["event_type"], "occurred_at": iso_from_ts(row["occurred_at"]),
            "details": json.loads(row["details_json"]),
        } for row in rows]})

    def _my_rating(self) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        with database() as db:
            summary = rating_summary(db, account["id"], account["role"])
        self._send_json(200, {"profile_id": account["id"], "role": account["role"], **summary})

    def _profile_rating(self, profile_id: str) -> None:
        account_from_token(self.headers.get("Authorization"))
        with database() as db:
            profile = db.execute(
                "SELECT id,role FROM accounts WHERE id=? AND status='active'", (profile_id,)
            ).fetchone()
            if not profile:
                raise APIError(404, "Perfil nÃ£o encontrado.")
            summary = rating_summary(db, profile["id"], profile["role"])
        self._send_json(200, {"profile_id": profile["id"], "role": profile["role"], **summary})

    def _submit_rating(self, booking_id: str) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        data = self._json_body()
        stars_value = data.get("stars")
        if isinstance(stars_value, bool) or not isinstance(stars_value, int) or stars_value not in range(1, 6):
            raise APIError(400, "A avaliaÃ§Ã£o deve ser um nÃºmero inteiro de 1 a 5 estrelas.")

        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            booking = db.execute(
                "SELECT b.id,b.passenger_id,b.offer_id,b.status,o.driver_id,o.departure_at,o.status AS offer_status "
                "FROM bookings b JOIN offers o ON o.id=b.offer_id WHERE b.id=?", (booking_id,)
            ).fetchone()
            if not booking:
                raise APIError(404, "Viagem nÃ£o encontrada.")
            if account["id"] == booking["passenger_id"]:
                target_id = booking["driver_id"]
            elif account["id"] == booking["driver_id"]:
                target_id = booking["passenger_id"]
            else:
                raise APIError(403, "Somente participantes desta viagem podem avaliÃ¡-la.")
            if booking["status"] != "confirmed" or booking["offer_status"] != "active":
                raise APIError(409, "Somente viagens concluÃ­das e nÃ£o canceladas podem ser avaliadas.")
            departure_at = datetime.fromisoformat(booking["departure_at"].replace("Z", "+00:00"))
            if departure_at > datetime.now(timezone.utc):
                raise APIError(409, "A avaliaÃ§Ã£o fica disponÃ­vel apÃ³s o horÃ¡rio da viagem.")
            already_rated = db.execute(
                "SELECT 1 FROM ratings WHERE booking_id=? AND reviewer_id=?", (booking_id, account["id"])
            ).fetchone()
            if already_rated:
                raise APIError(409, "VocÃª jÃ¡ avaliou esta viagem.")
            db.execute(
                "INSERT INTO ratings(id,booking_id,reviewer_id,target_id,stars,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), booking_id, account["id"], target_id, stars_value, now_ts()),
            )
            target = db.execute("SELECT role FROM accounts WHERE id=?", (target_id,)).fetchone()
            summary = rating_summary(db, target_id, target["role"])
        self._send_json(201, {"submitted": True, "profile_id": target_id, **summary})

    def _admin_dashboard(self) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        admin_ids = {item.strip() for item in os.environ.get("ADMIN_ACCOUNT_IDS", "").split(",") if item.strip()}
        if not admin_ids:
            raise APIError(503, "O painel administrativo ainda nÃ£o foi configurado.")
        if account["id"] not in admin_ids:
            raise APIError(403, "Acesso restrito Ã  administraÃ§Ã£o.")

        local_now = datetime.now(REPORTING_TIMEZONE)
        local_today = local_now.date()
        today_start = datetime.combine(local_today, datetime.min.time(), REPORTING_TIMEZONE)
        tomorrow_start = datetime.combine(local_today + timedelta(days=1), datetime.min.time(), REPORTING_TIMEZONE)
        month_start = today_start.replace(day=1)
        today_from = int(today_start.timestamp())
        tomorrow_from = int(tomorrow_start.timestamp())
        month_from = int(month_start.timestamp())
        now_departure = datetime.now(timezone.utc).isoformat()

        with database() as db:
            total_users = db.execute(
                "SELECT COUNT(*) AS total, SUM(role='driver') AS drivers, SUM(role='passenger') AS passengers "
                "FROM accounts WHERE status='active'"
            ).fetchone()
            cars = db.execute(
                "SELECT car_make,car_model,COUNT(*) AS quantity FROM accounts "
                "WHERE role='driver' AND status='active' AND car_make IS NOT NULL AND car_model IS NOT NULL "
                "GROUP BY car_make,car_model ORDER BY quantity DESC,car_make,car_model"
            ).fetchall()

            def period_summary(start: int, end: int) -> dict[str, Any]:
                new_users = db.execute(
                    "SELECT COUNT(*) AS total,SUM(role='driver') AS drivers,SUM(role='passenger') AS passengers "
                    "FROM accounts WHERE created_at>=? AND created_at<?", (start, end)
                ).fetchone()
                login_counts = db.execute(
                    "SELECT SUM(event_type='auth.login_succeeded') AS succeeded, "
                    "SUM(event_type IN ('auth.login_failed','auth.login_blocked','auth.login_rate_limited')) AS failed "
                    "FROM audit_log WHERE occurred_at>=? AND occurred_at<? "
                    "AND event_type IN ('auth.login_succeeded','auth.login_failed','auth.login_blocked','auth.login_rate_limited')",
                    (start, end),
                ).fetchone()
                offer_counts = db.execute(
                    "SELECT COUNT(*) AS total,SUM(status='active') AS active,SUM(total_seats) AS seats "
                    "FROM offers WHERE created_at>=? AND created_at<?", (start, end)
                ).fetchone()
                bookings = db.execute(
                    "SELECT COUNT(*) AS total,COALESCE(SUM(o.price_cents),0) AS value_cents "
                    "FROM bookings b JOIN offers o ON o.id=b.offer_id "
                    "WHERE b.status='confirmed' AND b.created_at>=? AND b.created_at<?", (start, end)
                ).fetchone()
                cancellations = db.execute(
                    "SELECT COUNT(*) AS total FROM bookings WHERE cancelled_at>=? AND cancelled_at<?",
                    (start, end),
                ).fetchone()["total"]
                open_seats = db.execute(
                    "SELECT COALESCE(SUM(o.total_seats-(SELECT COUNT(*) FROM bookings b "
                    "WHERE b.offer_id=o.id AND b.status='confirmed')),0) AS total "
                    "FROM offers o WHERE o.created_at>=? AND o.created_at<? AND o.status='active' AND o.departure_at>?",
                    (start, end, now_departure),
                ).fetchone()["total"]
                return {
                    "new_users": {
                        "total": new_users["total"], "drivers": new_users["drivers"] or 0,
                        "passengers": new_users["passengers"] or 0,
                    },
                    "logins": {"successful": login_counts["succeeded"] or 0, "failed": login_counts["failed"] or 0},
                    "offers": {
                        "published": offer_counts["total"], "currently_active": offer_counts["active"] or 0,
                        "seats_offered": offer_counts["seats"] or 0, "seats_available_now": open_seats,
                    },
                    "trips": {"accepted": bookings["total"], "cancelled_reservations": cancellations},
                    "confirmed_reservations_value": {
                        "amount_cents": bookings["value_cents"], "currency": "BRL",
                        "amount_brl": round(bookings["value_cents"] / 100, 2),
                    },
                }

            hourly = [{
                "hour": f"{hour:02d}:00", "new_users": 0, "successful_logins": 0, "failed_logins": 0,
            } for hour in range(24)]
            event_rows = db.execute(
                "SELECT event_type,occurred_at FROM audit_log WHERE occurred_at>=? AND occurred_at<? "
                "AND event_type IN ('account.created','auth.login_succeeded','auth.login_failed','auth.login_blocked','auth.login_rate_limited')",
                (today_from, tomorrow_from),
            ).fetchall()
            for event in event_rows:
                local_hour = datetime.fromtimestamp(event["occurred_at"], REPORTING_TIMEZONE).hour
                bucket = hourly[local_hour]
                if event["event_type"] == "account.created":
                    bucket["new_users"] += 1
                elif event["event_type"] == "auth.login_succeeded":
                    bucket["successful_logins"] += 1
                else:
                    bucket["failed_logins"] += 1

            self._write_audit(db, "admin.dashboard_viewed", account["id"], {
                "date": local_today.isoformat(), "month": local_today.strftime("%Y-%m"),
            })

            dashboard = {
                "timezone": "America/Sao_Paulo",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "date": local_today.isoformat(),
                "month": local_today.strftime("%Y-%m"),
                "user_flow_by_hour": hourly,
                "today": period_summary(today_from, tomorrow_from),
                "month_to_date": period_summary(month_from, tomorrow_from),
                "users_total": {
                    "active": total_users["total"] or 0, "drivers": total_users["drivers"] or 0,
                    "passengers": total_users["passengers"] or 0,
                },
                "fleet": {
                    "registered_cars": sum(row["quantity"] for row in cars),
                    "by_make_model": [{"make": row["car_make"], "model": row["car_model"],
                                       "quantity": row["quantity"]} for row in cars],
                },
                "financial_note": "Valor das reservas confirmadas; nÃ£o representa pagamento liquidado.",
            }
        self._send_json(200, dashboard)

    def _admin_dashboard_page(self) -> None:
        page = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VaiProPolo · Administração</title><style>
:root{color-scheme:light;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f3f6fa;color:#152238}
*{box-sizing:border-box}body{margin:0}header{background:#102a43;color:white;padding:22px max(24px,calc((100% - 1180px)/2));display:flex;align-items:center;justify-content:space-between}
header h1{font-size:20px;margin:0}main{max-width:1180px;margin:28px auto;padding:0 20px}.muted{color:#66768a}.panel,.card{background:white;border:1px solid #e0e7ef;border-radius:14px;padding:20px;box-shadow:0 4px 16px #102a4308}
#login{max-width:420px;margin:8vh auto}label{display:block;font-size:14px;margin:14px 0 6px}input{width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:8px;font-size:16px}
button{border:0;border-radius:8px;padding:11px 16px;background:#1769aa;color:white;font-weight:650;cursor:pointer}button.secondary{background:#31506d}button:disabled{opacity:.55;cursor:wait}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:18px 0}.card span{display:block}.card .label{font-size:13px;color:#66768a}.card .value{font-size:26px;font-weight:750;margin:8px 0}.card .sub{font-size:13px;color:#66768a}
.columns{display:grid;grid-template-columns:1.5fr 1fr;gap:16px}.panel h2{font-size:17px;margin:0 0 6px}.hour-list{margin-top:16px;display:grid;gap:7px}.hour{display:grid;grid-template-columns:44px 1fr;gap:10px;align-items:center;font-size:12px}.bars{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));height:16px;gap:3px}.bar{min-width:0;border-radius:3px}.users{background:#24a678}.success{background:#3785d5}.failed{background:#e17c63}.legend{display:flex;gap:14px;font-size:12px;margin-top:12px;color:#52647a}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
.table{width:100%;border-collapse:collapse;margin-top:12px;font-size:14px}.table th,.table td{text-align:left;border-bottom:1px solid #e8edf3;padding:10px 6px}.table th{color:#66768a;font-weight:600}#dashboard[hidden],#login[hidden]{display:none!important}.error{color:#b42318;margin-top:12px;min-height:20px}.topline{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.topline p{margin:4px 0 0}
@media(max-width:850px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.columns{grid-template-columns:1fr}}@media(max-width:460px){.grid{grid-template-columns:1fr 1fr}.card{padding:14px}.card .value{font-size:21px}}
</style></head><body><header><h1>VaiProPolo <span style="font-weight:400;opacity:.72">/ Administração</span></h1><span id="date" class="muted" style="color:#c5d3e0"></span></header>
<main><section id="login" class="panel"><h2>Acesso administrativo</h2><p class="muted">Entre com uma conta autorizada pela equipe.</p><form id="loginForm"><label for="email">E-mail</label><input id="email" type="email" autocomplete="username" required><label for="password">Senha</label><input id="password" type="password" autocomplete="current-password" required><p id="loginError" class="error" role="alert"></p><button id="loginButton" type="submit">Entrar</button></form></section>
<section id="dashboard" hidden><div class="topline"><div><h2>Painel de indicadores</h2><p id="period" class="muted"></p></div><button id="refresh" class="secondary">Atualizar</button></div>
<div class="grid"><article class="card"><span class="label">Usuários ativos</span><span id="users" class="value">—</span><span id="usersSub" class="sub"></span></article><article class="card"><span class="label">Viagens aceitas hoje</span><span id="tripsToday" class="value">—</span><span id="valueToday" class="sub"></span></article><article class="card"><span class="label">Reservas no mês</span><span id="tripsMonth" class="value">—</span><span id="valueMonth" class="sub"></span></article><article class="card"><span class="label">Carros cadastrados</span><span id="cars" class="value">—</span><span id="carsSub" class="sub"></span></article></div>
<div class="grid"><article class="card"><span class="label">Ofertas publicadas hoje</span><span id="offersToday" class="value">—</span><span id="seatsToday" class="sub"></span></article><article class="card"><span class="label">Ofertas publicadas no mês</span><span id="offersMonth" class="value">—</span><span id="seatsMonth" class="sub"></span></article><article class="card"><span class="label">Reservas canceladas hoje</span><span id="cancelToday" class="value">—</span><span class="sub">Cancelamentos registrados no período</span></article><article class="card"><span class="label">Logins hoje</span><span id="loginsToday" class="value">—</span><span id="loginsSub" class="sub"></span></article></div>
<div class="columns"><section class="panel"><h2>Fluxo de usuários por hora</h2><p class="muted">Novos cadastros e tentativas de login no horário de Brasília.</p><div id="hourly" class="hour-list"></div><div class="legend"><span><i class="dot users"></i>Cadastros</span><span><i class="dot success"></i>Login aceito</span><span><i class="dot failed"></i>Login recusado</span></div></section>
<section class="panel"><h2>Frota por marca e modelo</h2><p class="muted">Veículos de motoristas com conta ativa.</p><table class="table"><thead><tr><th>Marca</th><th>Modelo</th><th>Carros</th></tr></thead><tbody id="fleet"></tbody></table><p id="financialNote" class="muted" style="font-size:12px;margin-top:16px"></p></section></div></section></main>
<script>
let token=null;const money=new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'});const el=id=>document.getElementById(id);const number=value=>new Intl.NumberFormat('pt-BR').format(value||0);
async function request(path,options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{}),...(options.headers||{})}});const data=await response.json();if(!response.ok)throw new Error(data.error||'Não foi possível carregar os dados.');return data}
el('loginForm').addEventListener('submit',async event=>{event.preventDefault();el('loginError').textContent='';el('loginButton').disabled=true;try{const result=await request('/v1/auth/login',{method:'POST',body:JSON.stringify({email:el('email').value,password:el('password').value})});token=result.access_token;await loadDashboard()}catch(error){token=null;el('loginError').textContent=error.message}finally{el('loginButton').disabled=false}});
el('refresh').addEventListener('click',loadDashboard);
function card(id,value){el(id).textContent=value}
async function loadDashboard(){try{const d=await request('/v1/admin/dashboard');el('login').hidden=true;el('dashboard').hidden=false;el('date').textContent=d.date+' · '+d.timezone;el('period').textContent='Hoje: '+d.date+' · Mês: '+d.month;
card('users',number(d.users_total.active));card('usersSub',number(d.users_total.drivers)+' motoristas · '+number(d.users_total.passengers)+' passageiros');
card('tripsToday',number(d.today.trips.accepted));card('valueToday',money.format(d.today.confirmed_reservations_value.amount_brl)+' em reservas confirmadas');card('tripsMonth',number(d.month_to_date.trips.accepted));card('valueMonth',money.format(d.month_to_date.confirmed_reservations_value.amount_brl)+' em reservas confirmadas');
card('cars',number(d.fleet.registered_cars));card('carsSub',number(d.users_total.drivers)+' motoristas ativos');card('offersToday',number(d.today.offers.published));card('seatsToday',number(d.today.offers.seats_offered)+' vagas · '+number(d.today.offers.seats_available_now)+' livres agora');card('offersMonth',number(d.month_to_date.offers.published));card('seatsMonth',number(d.month_to_date.offers.seats_offered)+' vagas · '+number(d.month_to_date.offers.seats_available_now)+' livres agora');card('cancelToday',number(d.today.trips.cancelled_reservations));card('loginsToday',number(d.today.logins.successful));card('loginsSub',number(d.today.logins.failed)+' recusados');card('financialNote',d.financial_note);
const max=Math.max(1,...d.user_flow_by_hour.flatMap(h=>[h.new_users,h.successful_logins,h.failed_logins]));const list=el('hourly');list.replaceChildren();for(const h of d.user_flow_by_hour){const row=document.createElement('div');row.className='hour';const label=document.createElement('span');label.textContent=h.hour;const bars=document.createElement('div');bars.className='bars';for(const [key,cls,title] of [['new_users','users','Cadastros'],['successful_logins','success','Logins aceitos'],['failed_logins','failed','Logins recusados']]){const bar=document.createElement('span');bar.className='bar '+cls;bar.style.width=(h[key]/max*100)+'%';bar.title=title+': '+h[key];bars.append(bar)}row.append(label,bars);list.append(row)}
const tbody=el('fleet');tbody.replaceChildren();if(!d.fleet.by_make_model.length){const row=document.createElement('tr');const cell=document.createElement('td');cell.colSpan=3;cell.textContent='Nenhum veículo cadastrado';row.append(cell);tbody.append(row)}else for(const car of d.fleet.by_make_model){const row=document.createElement('tr');for(const value of [car.make,car.model,number(car.quantity)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell)}tbody.append(row)}
}catch(error){if(error.message.includes('administra')||error.message.includes('restrito')){token=null;el('dashboard').hidden=true;el('login').hidden=false;el('loginError').textContent=error.message}else if(!token){el('loginError').textContent=error.message}else{alert(error.message)}}}
</script></body></html>""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(page)

    def _mark_notification_read(self, notification_id: str) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        data = self._json_body()
        if data.get("confirm") is not True:
            raise APIError(400, "Confirme a leitura da notificação enviando confirm=true.")
        with database() as db:
            cursor = db.execute(
                "UPDATE notifications SET read_at=? WHERE id=? AND account_id=? AND read_at IS NULL",
                (now_ts(), notification_id, account["id"]),
            )
            if cursor.rowcount == 0:
                raise APIError(404, "Notificação não encontrada ou já lida.")
        self._send_json(200, {"read": True, "notification_id": notification_id})

    def _update_offer(self, offer_id: str) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        if account["role"] != "driver":
            raise APIError(403, "Somente motoristas podem alterar ofertas.")
        data = self._json_body()
        values: dict[str, Any] = {}
        if "origin" in data:
            origin = str(data["origin"]).strip()
            if not origin or len(origin) > 200:
                raise APIError(400, "Informe um ponto de partida válido.")
            values["origin"] = origin
        if "departure_at" in data:
            try:
                departure = datetime.fromisoformat(str(data["departure_at"]).replace("Z", "+00:00"))
            except ValueError as error:
                raise APIError(400, "departure_at deve ser uma data ISO-8601.") from error
            if departure.tzinfo is None or departure <= datetime.now(timezone.utc):
                raise APIError(400, "A partida precisa ser futura e incluir fuso horário.")
            values["departure_at"] = departure.astimezone(timezone.utc).isoformat()
        if "price" in data:
            try:
                price = Decimal(str(data["price"]))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise APIError(400, "Valor inválido.") from error
            if not price.is_finite() or price <= 0 or price > Decimal("999999.99"):
                raise APIError(400, "O valor precisa ser maior que zero e no máximo R$ 999.999,99.")
            values["price_cents"] = int((price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if "total_seats" in data:
            if isinstance(data["total_seats"], bool):
                raise APIError(400, "total_seats deve ser um número entre 1 e 4.")
            try:
                seats = int(data["total_seats"])
            except (TypeError, ValueError) as error:
                raise APIError(400, "total_seats deve ser um número entre 1 e 4.") from error
            if seats not in range(1, 5):
                raise APIError(400, "A capacidade total deve ficar entre 1 e 4 vagas.")
            values["total_seats"] = seats
            values["available_seats"] = seats
        if not values:
            raise APIError(400, "Envie ao menos um dado alterável: origem, data, valor ou total_seats.")

        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            offer = db.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()
            if not offer or offer["driver_id"] != account["id"]:
                raise APIError(404, "Oferta não encontrada.")
            if offer["status"] != "active":
                raise APIError(409, "Não é possível alterar uma oferta cancelada.")
            if datetime.fromisoformat(offer["departure_at"]) <= datetime.now(timezone.utc):
                raise APIError(409, "Não é possível alterar uma oferta cuja partida já ocorreu.")
            confirmed = db.execute(
                "SELECT COUNT(*) AS count FROM bookings WHERE offer_id=? AND status='confirmed'", (offer_id,)
            ).fetchone()["count"]
            if confirmed and any(column in values for column in ("origin", "departure_at", "price_cents")):
                raise APIError(409, "Não altere origem, data ou valor enquanto houver passageiros reservados. Cancele e publique outra oferta.")
            if "total_seats" in values and values["total_seats"] < confirmed:
                raise APIError(409, f"Já existem {confirmed} reserva(s); a capacidade não pode ser menor que isso.")
            assignments = ",".join(f"{column}=?" for column in values)
            db.execute(f"UPDATE offers SET {assignments} WHERE id=?", (*values.values(), offer_id))
            self._write_audit(db, "offer.updated", account["id"], {
                "offer_id": offer_id, "changed_fields": sorted(values),
                "before": {"origin": offer["origin"], "departure_at": offer["departure_at"],
                           "price_cents": offer["price_cents"], "total_seats": offer["total_seats"]},
                "after": {"origin": values.get("origin", offer["origin"]),
                          "departure_at": values.get("departure_at", offer["departure_at"]),
                          "price_cents": values.get("price_cents", offer["price_cents"]),
                          "total_seats": values.get("total_seats", offer["total_seats"])},
                "currency": "BRL",
            })
        self._send_json(200, {"updated": True, "offer_id": offer_id})

    def _cancel_offer(self, offer_id: str) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        if account["role"] != "driver":
            raise APIError(403, "Somente motoristas podem desistir de uma oferta.")
        confirmation = self._json_body()
        if confirmation.get("confirm") is not True:
            raise APIError(400, "Confirme o cancelamento enviando confirm=true.")
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            offer = db.execute("SELECT driver_id,status FROM offers WHERE id=?", (offer_id,)).fetchone()
            if not offer or offer["driver_id"] != account["id"]:
                raise APIError(404, "Oferta não encontrada.")
            if offer["status"] != "active":
                raise APIError(409, "A oferta já foi cancelada.")
            passengers = db.execute(
                "SELECT id,passenger_id FROM bookings WHERE offer_id=? AND status='confirmed'", (offer_id,)
            ).fetchall()
            for passenger in passengers:
                db.execute(
                    "INSERT INTO notifications(id,account_id,booking_id,kind,message,created_at) "
                    "VALUES(?,?,?,'driver_cancelled_offer',?,?)",
                    (str(uuid.uuid4()), passenger["passenger_id"], passenger["id"],
                     "O motorista cancelou a oferta. A reserva foi cancelada.", now_ts()),
                )
            db.execute("UPDATE offers SET status='cancelled' WHERE id=?", (offer_id,))
            db.execute(
                "UPDATE bookings SET status='cancelled_by_driver',cancelled_at=? WHERE offer_id=? AND status='confirmed'",
                (now_ts(), offer_id),
            )
            self._write_audit(db, "offer.cancelled", account["id"], {
                "offer_id": offer_id, "affected_passengers": len(passengers),
            })
        self._send_json(200, {"cancelled": True, "offer_id": offer_id})

    def _book_offer(self, offer_id: str) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        if account["role"] != "passenger":
            raise APIError(403, "Somente passageiros podem reservar vagas.")
        current_dt = datetime.now(timezone.utc)
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            offer = db.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()
            if not offer or offer["status"] != "active":
                raise APIError(404, "Oferta não encontrada ou cancelada.")
            if datetime.fromisoformat(offer["departure_at"]) <= current_dt:
                raise APIError(409, "Não é possível reservar uma oferta cuja partida já ocorreu.")
            existing = db.execute(
                "SELECT id FROM bookings WHERE offer_id=? AND passenger_id=? AND status='confirmed'",
                (offer_id, account["id"]),
            ).fetchone()
            if existing:
                raise APIError(409, "Você já tem uma reserva ativa nesta oferta.")
            count = db.execute(
                "SELECT COUNT(*) AS count FROM bookings WHERE offer_id=? AND status='confirmed'", (offer_id,)
            ).fetchone()["count"]
            if count >= offer["total_seats"]:
                raise APIError(409, "Não há mais vagas disponíveis.")
            booking_id = str(uuid.uuid4())
            occurred_at = now_ts()
            db.execute(
                "INSERT INTO bookings(id,offer_id,passenger_id,status,created_at) VALUES(?,?,?,'confirmed',?)",
                (booking_id, offer_id, account["id"], occurred_at),
            )
            self._write_audit(db, "booking.accepted", account["id"], {
                "booking_id": booking_id, "offer_id": offer_id,
                "driver_id": offer["driver_id"], "origin": offer["origin"],
                "destination": offer["destination"], "departure_at": offer["departure_at"],
                "price_cents": offer["price_cents"], "currency": "BRL",
                "passenger_count_after_booking": count + 1,
                "available_seats_after_booking": offer["total_seats"] - count - 1,
            })
        self._send_json(201, {"booking_id": booking_id, "status": "confirmed", "available_seats": offer["total_seats"] - count - 1})

    def _cancel_booking(self, booking_id: str) -> None:
        account = account_from_token(self.headers.get("Authorization"))
        if account["role"] != "passenger":
            raise APIError(403, "Somente passageiros podem cancelar uma reserva.")
        confirmation = self._json_body()
        if confirmation.get("confirm") is not True:
            raise APIError(400, "Confirme o cancelamento enviando confirm=true.")
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            booking = db.execute(
                "SELECT b.*,o.departure_at,o.status AS offer_status FROM bookings b JOIN offers o ON o.id=b.offer_id WHERE b.id=?",
                (booking_id,),
            ).fetchone()
            if not booking or booking["passenger_id"] != account["id"]:
                raise APIError(404, "Reserva não encontrada.")
            if booking["status"] != "confirmed":
                raise APIError(409, "A reserva não está mais ativa.")
            if booking["offer_status"] != "active" or datetime.fromisoformat(booking["departure_at"]) <= datetime.now(timezone.utc):
                raise APIError(409, "Esta reserva não pode mais ser cancelada.")
            db.execute(
                "UPDATE bookings SET status='cancelled_by_passenger',cancelled_at=? WHERE id=?",
                (now_ts(), booking_id),
            )
            self._write_audit(db, "booking.cancelled_by_passenger", account["id"], {
                "booking_id": booking_id, "offer_id": booking["offer_id"],
                "departure_at": booking["departure_at"],
            })
            remaining = db.execute(
                "SELECT total_seats-(SELECT COUNT(*) FROM bookings WHERE offer_id=? AND status='confirmed') AS seats_left "
                "FROM offers WHERE id=?", (booking["offer_id"], booking["offer_id"]),
            ).fetchone()["seats_left"]
        self._send_json(200, {"cancelled": True, "available_seats": remaining})

    def _verification_landing_page(self) -> None:
        body = """<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Desbloquear conta</title><body><main><h1>Confirmação de e-mail</h1><p>Confirme seu e-mail para desbloquear sua conta VaiProPolo.</p><button id="confirm">Desbloquear minha conta</button><p id="result" role="status"></p></main><script>
const token = new URLSearchParams(location.hash.slice(1)).get('token');
history.replaceState(null, '', location.pathname);
document.getElementById('confirm').onclick = async () => {{
  const result = document.getElementById('result');
  const button = document.getElementById('confirm'); button.disabled = true;
  try {{
    const response = await fetch('/v1/verification/email/confirm', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{token}})}});
    const data = await response.json();
    result.textContent = response.ok ? 'E-mail confirmado. Sua conta foi desbloqueada.' : (data.error || 'Link inválido, expirado ou já utilizado.');
  }} catch {{ result.textContent = 'Não foi possível concluir. Tente novamente.'; button.disabled = false; }}
}};
</script></body></html>""".replace("{{", "{").replace("}}", "}").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    initialize_database()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"VaiProPolo API disponível em http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
