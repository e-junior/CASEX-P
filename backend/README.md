# VaiProPolo API

Small Python 3.12+ API for account verification and ride offers. It uses only the Python standard library and SQLite. It is a backend starter: deploy it behind an HTTPS reverse proxy, restrict the CORS origin if a browser client is used, and keep the database and CNH directory private.

## Start locally

Set the variables from `.env.example` in the shell, then run:

```powershell
$env:VERIFICATION_HMAC_SECRET = 'replace-with-at-least-32-random-characters'
$env:PUBLIC_BASE_URL = 'http://localhost:8000'
& 'C:/Users/eleci/AppData/Local/Programs/Python/Python312/python.exe' .\backend\server.py
```

Generate a suitable secret with Python's `secrets.token_urlsafe(48)`. In production, use a secret manager, HTTPS, a real hostname in `PUBLIC_BASE_URL`, backups for the SQLite data, and private encrypted storage for driver documents. Do not commit secrets, the database, or uploaded CNHs.

SMS and WhatsApp use Twilio's messaging API. WhatsApp requires an approved content template and its Content SID in `TWILIO_WHATSAPP_CONTENT_SID`; pass the verification code as template variable `{{1}}`. E-mail uses SMTP with STARTTLS by default (or SSL with `SMTP_USE_SSL=true`). The server returns a configuration error for delivery requests until the relevant provider variables are set.

## API

All request and response bodies are JSON unless noted. Phone numbers use E.164 format (`+5522...`).

| Method and path | Purpose |
| --- | --- |
| `GET /v1/health` | Health status |
| `POST /v1/verification/phone/request` | `{ "phone": "+5522999999999", "channel": "sms" }` or `whatsapp`; sends a cryptographically random six-digit code, expires in 10 minutes, resend after 120 seconds |
| `POST /v1/verification/phone/confirm` | `{ "phone": "+5522999999999", "code": "123456" }`; max five attempts |
| `POST /v1/verification/email/request` | `{ "email": "person@example.com", "name": "Full Name" }`; sends one-time link expiring in 24 hours, resend after 120 seconds |
| `POST /v1/verification/email/confirm` | `{ "token": "..." }`; confirms once and activates an account waiting for email confirmation |
| `GET /verify-email#token=...` | Confirmation page. The token is in the URL fragment, so it is not sent in the HTTP request or server access log; user action posts it to the confirmation endpoint |
| `POST /v1/accounts/register` | Creates a passenger or driver account; phone must be verified. If email is unverified, the account is created as `pending_email` and login is blocked |
| `POST /v1/auth/login` | `{ "email": "...", "password": "..." }`; returns a 12-hour Bearer token only for active accounts |
| `POST /v1/auth/logout` | Revokes the Bearer session |
| `GET /v1/me/activity` | Lists up to 200 audit events for the authenticated account |
| `GET /v1/admin/dashboard` | Admin-only dashboard metrics: hourly user/login flow, today's and month's offers/reservations/value/seats, and registered vehicle fleet |
| `GET /admin` | Browser dashboard with admin login, KPI cards, hourly user/login chart, and vehicle table |
| `GET /v1/offers` | Lists future offers for authenticated accounts |
| `POST /v1/offers` | Authenticated driver publishes an offer; destination is fixed to Polo Universitário de Macaé, seats are 1–4 |
| `GET /v1/me/offers` | Driver lists their own active and cancelled offers |
| `PATCH /v1/offers/{offer_id}` | Driver edits their own offer fields: `origin`, `departure_at`, `price`, and/or `total_seats` (1–4). Capacity cannot be set below confirmed reservations |
| `DELETE /v1/offers/{offer_id}` | Driver cancels their own offer; send `{ "confirm": true }` after the app's confirmation prompt. It is soft-cancelled and active reservations are marked cancelled by the driver |
| `POST /v1/offers/{offer_id}/book` | Passenger reserves one seat; capacity is checked atomically to prevent overbooking |
| `GET /v1/me/bookings` | Passenger lists active and cancelled reservations |
| `GET /v1/me/trips` | Driver lists passengers and reservations for their offers, with completed trips eligible for rating |
| `DELETE /v1/bookings/{booking_id}` | Passenger cancels their own active reservation before departure; send `{ "confirm": true }` after the app's confirmation prompt; the seat becomes available again |
| `POST /v1/bookings/{booking_id}/ratings` | A driver or passenger who took the trip can submit `{ "stars": 1 }` through `{ "stars": 5 }` once after departure |
| `GET /v1/me/rating` | Returns the authenticated user's aggregate rating |
| `GET /v1/profiles/{account_id}/rating` | Returns only an active profile's aggregate rating, never individual reviewers or ratings |
| `GET /v1/me/notifications` | Passenger lists unread notifications; driver cancellations include `presentation: "popup"` for the app to show as a pop-up |
| `POST /v1/notifications/{notification_id}/read` | Mark a passenger notification as read; requires `{ "confirm": true }` |

Registration body:

```json
{
  "role": "passenger",
  "full_name": "Full Name",
  "email": "person@example.com",
  "phone": "+5522999999999",
  "cpf": "52998224725",
  "password": "use-a-long-unique-password"
}
```

For `role: "driver"`, also send `car_make`, `car_model`, `plate` (seven characters), and `cnh_pdf_base64` (PDF up to 50 MB). The file is stored outside HTTP routes under `PRIVATE_UPLOAD_DIR` with a random filename and restrictive file permissions. The response never returns its path. The JSON request limit is 70 MiB to account for Base64 expansion.

Offer creation body:

```json
{
  "origin": "Centro de Macaé",
  "departure_at": "2026-11-01T08:30:00-03:00",
  "price": 25.5,
  "available_seats": 3
}
```

Use `Authorization: Bearer <access_token>` for offer requests and logout. Store the token in iOS Keychain, not `UserDefaults`.

## Banco de dados e auditoria

SQLite creates and migrates its tables automatically at server startup. `accounts` stores driver and passenger profiles, `sessions` stores only a keyed digest of each bearer token, `offers` stores ride details, and `bookings` stores passenger acceptance/cancellation state. CNH PDF files remain in the private upload directory; the database stores only their server-side path.

`audit_log` records account creation, successful/failed/blocked login, logout, offer creation/edit/cancellation, and booking acceptance/cancellation. Each event has a UTC timestamp, actor account when known, source IP, user agent, and JSON details. Offer creation and passenger acceptance events include origin, destination, departure date/time (ISO-8601), price in integer centavos (`price_cents`) and currency (`BRL`); acceptance also captures occupied and remaining seats. These event details are snapshots, so later edits do not rewrite history. `GET /v1/me/activity` exposes an account's own events only.

Access logs include IP addresses and user-agent strings, which are personal data. Define and enforce a retention period, backups, access controls, and a deletion/anonymization policy appropriate to the service before production. The app can display the current user's history by calling `GET /v1/me/activity` with its bearer token.

Ratings are available after the scheduled departure of a non-cancelled, confirmed trip. Each participant can rate the other once, from 1 to 5 stars. Profile summaries use up to the last 30 completed trips; for drivers, a trip's score is the average of its passenger ratings, then each trip receives equal weight. The displayed score is the arithmetic mean of stars and never exceeds 5; the API returns `stars_average` (one decimal) and `stars_average_exact` (three decimals), with no percentage score. It also returns review count and number of rated trips in the sample. Passenger booking responses include the driver's rating; driver trip responses include each passenger's rating. Public profile/offer responses show only these aggregates. The database privately retains the reviewer account ID to enforce that only trip participants rate and to prevent duplicate ratings; the other participant cannot see reviewer identity or individual ratings. This is anonymous to profile viewers, not anonymous to the service operator.

Configure `ADMIN_ACCOUNT_IDS` with comma-separated account UUIDs in the server environment before using `GET /v1/admin/dashboard`. An administrator authenticates through the regular login endpoint; the account ID must be allowlisted. An empty allowlist disables the dashboard, and ordinary driver/passenger accounts receive `403`. The response uses `America/Sao_Paulo` calendar boundaries, includes all 24 hourly buckets for today's new registrations and successful/failed logins, and separates today's values from month-to-date values. “Trips accepted” means confirmed passenger reservations created in the period; reservation value sums their current confirmed offer prices. It is a booking value, not proof of payment. Seat capacity sums published offers in the period; available seats counts only still-active future offers from that period. Fleet counts active driver accounts and groups their registered cars by make/model.

Offer edits use `PATCH /v1/offers/{offer_id}` with any subset of `origin`, `departure_at`, `price`, and `total_seats`. `total_seats` represents the vehicle capacity, including seats already reserved; the API rejects a capacity lower than the active reservation count. Passenger cancellation and driver cancellation are retained as booking/offer status changes for an audit trail. A driver cancellation creates a persistent unread notification for each affected passenger. The app should query `GET /v1/me/notifications`, present each item marked `presentation: "popup"` in a pop-up, then acknowledge it with `POST /v1/notifications/{notification_id}/read` and `{ "confirm": true }`. Both cancellation endpoints reject a request unless it explicitly contains `{ "confirm": true }`; the client should first ask for confirmation and send that value only after the user agrees.

## Security behavior

- OTP values and email tokens are generated with `secrets`; only keyed digests are stored.
- OTP resend is throttled at 120 seconds, OTPs expire after 10 minutes, and five failed attempts invalidate the challenge.
- Email links are single-use and expire after 24 hours. The link body is signed by **Equipe VaiProPolo**.
- Email confirmation activates `pending_email` accounts. Login and offer access require an active account.
- Passwords use salted scrypt hashes. Login attempts are rate-limited by source IP and lock the e-mail for 15 minutes after five failures.
- CNH files are not served by this API. Configure a private encrypted volume and a retention/deletion policy before production.

The API does not yet implement payment or push notifications; cancellation pop-ups are delivered as in-app unread notifications when the passenger app polls the API. The existing SwiftUI screens also need an HTTP service adapter, booking/cancellation controls, notification polling and pop-up presentation, password fields, and universal-link association to call this API; the Swift app currently contains no backend URL or provider credentials. Set the reverse proxy to avoid logging query strings and request bodies; the verification token is carried in the URL fragment so it is not part of the initial HTTP request.
