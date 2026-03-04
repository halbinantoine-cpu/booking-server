import os
import json
import secrets
import hashlib
import base64
import requests as req
from flask import Flask, request, jsonify, redirect, session
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey123")

# === CONFIGURATION ===
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://booking-server-u1ep.onrender.com/oauth/callback")
SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = "/tmp/google_token.json"

RENDER_API_KEY = os.getenv("RENDER_API_KEY")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID")

PARIS_TZ = pytz.timezone("Europe/Paris")

# === HELPERS ===
def normalize_string(s):
    if not s:
        return ""
    return s.strip().lower().replace("_", "").replace("-", "")

def get_field(data, *keys, default=None):
    for key in keys:
        norm_key = normalize_string(key)
        for k, v in data.items():
            if normalize_string(k) == norm_key:
                return v
    return default

def save_refresh_token_to_render(refresh_token):
    """Sauvegarde le refresh_token dans les variables d'environnement Render."""
    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        print("RENDER_API_KEY ou RENDER_SERVICE_ID manquant", flush=True)
        return False
    try:
        response = req.put(
            f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars",
            headers={
                "Authorization": f"Bearer {RENDER_API_KEY}",
                "Content-Type": "application/json"
            },
            json=[{"key": "GOOGLE_REFRESH_TOKEN", "value": refresh_token}]
        )
        if response.status_code == 200:
            print("REFRESH TOKEN SAUVEGARDÉ DANS RENDER", flush=True)
            return True
        else:
            print(f"ERREUR RENDER API: {response.status_code} {response.text}", flush=True)
            return False
    except Exception as e:
        print(f"ERREUR SAVE RENDER: {e}", flush=True)
        return False

def load_google_credentials():
    """Charge les credentials depuis /tmp ou depuis la variable d'env GOOGLE_REFRESH_TOKEN."""
    # 1. Essaie depuis /tmp
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r") as f:
                token_data = json.load(f)
            creds = Credentials(**token_data)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_data["token"] = creds.token
                with open(TOKEN_FILE, "w") as f:
                    json.dump(token_data, f)
                print("GOOGLE TOKEN REFRESHED", flush=True)
            return creds
        except Exception as e:
            print(f"ERREUR LOAD TOKEN FILE: {e}", flush=True)

    # 2. Essaie depuis la variable d'env GOOGLE_REFRESH_TOKEN
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    if refresh_token:
        try:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET,
                scopes=SCOPES
            )
            creds.refresh(Request())
            # Sauvegarde dans /tmp pour les prochains appels
            token_data = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": SCOPES
            }
            with open(TOKEN_FILE, "w") as f:
                json.dump(token_data, f)
            print("GOOGLE TOKEN CHARGÉ DEPUIS ENV VAR", flush=True)
            return creds
        except Exception as e:
            print(f"ERREUR LOAD REFRESH TOKEN: {e}", flush=True)

    return None

# === ROUTES ===
@app.route("/health", methods=["GET"])
def health():
    token_source = "none"
    if os.path.exists(TOKEN_FILE):
        token_source = "file"
    elif os.getenv("GOOGLE_REFRESH_TOKEN"):
        token_source = "env_var"
    return jsonify(ok=True, service="booking-server", token_source=token_source), 200

@app.route("/oauth/start", methods=["GET"])
def oauth_start():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return jsonify(ok=False, error="missing_google_credentials"), 500

    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    session["code_verifier"] = code_verifier

    from urllib.parse import urlencode
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urlencode(params)
    return redirect(auth_url)

@app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    code = request.args.get("code")
    if not code:
        return jsonify(ok=False, error="no_code"), 400

    code_verifier = session.get("code_verifier")
    if not code_verifier:
        return jsonify(ok=False, error="missing_code_verifier"), 400

    token_response = req.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    })

    token_data = token_response.json()

    if "error" in token_data:
        return jsonify(ok=False, error=token_data["error"], details=token_data), 400

    refresh_token = token_data.get("refresh_token")

    # Sauvegarde dans /tmp
    with open(TOKEN_FILE, "w") as f:
        json.dump({
            "token": token_data.get("access_token"),
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "scopes": SCOPES,
        }, f)

    # Sauvegarde permanente dans Render
    if refresh_token:
        save_refresh_token_to_render(refresh_token)

    print("GOOGLE TOKEN SAVED", flush=True)
    return "<h2>✅ Authentification réussie ! Tu peux fermer cette page.</h2>", 200

@app.route("/book_appointment", methods=["POST"])
def book_appointment():
    expected = os.getenv("X_API_KEY", "")
    provided = request.headers.get("X-API-Key", "")

    if not expected or provided != expected:
        return jsonify(ok=False, error="unauthorized"), 401

    creds = load_google_credentials()
    if not creds:
        return jsonify(
            ok=False,
            error="not_authenticated",
            auth_url="https://booking-server-u1ep.onrender.com/oauth/start"
        ), 401

    data = request.get_json(silent=True) or {}

    customer_name = get_field(data, "customer_name", "customername", "nom", "name", "client", default="Client")
    service_type  = get_field(data, "service", "prestation", "type", "service_type", default="Prestation")
    phone         = get_field(data, "phone", "telephone", "tel", "numero", "number", default="Non fourni")
    notes         = get_field(data, "notes", "remarques", "commentaire", "comment", default="")
    start_time    = get_field(data, "start_time", "starttime", "date", "datetime", "start")

    if not start_time:
        return jsonify(ok=False, error="missing_start_time"), 400

    try:
        service = build("calendar", "v3", credentials=creds)

        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = PARIS_TZ.localize(start_dt)

        end_dt = start_dt + timedelta(hours=1)

        events_result = service.events().list(
            calendarId="primary",
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        existing_events = events_result.get("items", [])
        MAX_CONCURRENT = 3

        if len(existing_events) >= MAX_CONCURRENT:
            return jsonify(
                ok=False,
                error="slot_full",
                message=f"Ce créneau est complet ({len(existing_events)}/{MAX_CONCURRENT} RDV)"
            ), 409

        description_parts = [f"Client: {customer_name}"]
        if phone != "Non fourni":
            description_parts.append(f"Téléphone: {phone}")
        if notes:
            description_parts.append(f"Notes: {notes}")

        event = {
            "summary": f"{service_type} – {customer_name}",
            "description": "\n".join(description_parts),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Paris"},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "Europe/Paris"},
        }

        created_event = service.events().insert(calendarId="primary", body=event).execute()

        return jsonify(
            ok=True,
            message="Rendez-vous créé avec succès",
            event_id=created_event.get("id"),
            event_link=created_event.get("htmlLink")
        ), 200

    except Exception as e:
        print(f"CALENDAR ERROR: {e}", flush=True)
        return jsonify(ok=False, error="calendar_failed", details=str(e)), 500

# === LANCER LE SERVEUR ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
