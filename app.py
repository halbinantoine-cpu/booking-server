import os
import json
from flask import Flask, request, jsonify, redirect
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# === CONFIGURATION ===
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://booking-server-u1ep.onrender.com/oauth/callback")
SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = "/tmp/google_token.json"

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

# === ROUTES ===
@app.route("/health", methods=["GET"])
def health():
    return jsonify(ok=True, service="booking-server"), 200

@app.route("/oauth/start", methods=["GET"])
def oauth_start():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return jsonify(ok=False, error="missing_google_credentials"), 500

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

    auth_url, state = flow.authorization_url(prompt="consent", access_type="offline")
    # Sauvegarde le state pour vérification dans le callback
    os.environ["OAUTH_STATE"] = state
    return redirect(auth_url)

@app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    # ✅ CORRECTION PKCE : utilise authorization_response au lieu de code seul
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    state = request.args.get("state")
    if not state:
        return jsonify(ok=False, error="missing_state"), 400

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI],
            }
        },
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI,
    )

    # Utilise l'URL complète — résout l'erreur "code verifier manquant"
    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }

    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)

    print("GOOGLE TOKEN SAVED", flush=True)
    return "<h2>✅ Authentification réussie ! Tu peux fermer cette page.</h2>", 200

def load_google_credentials():
    if not os.path.exists(TOKEN_FILE):
        return None

    with open(TOKEN_FILE, "r") as f:
        token_data = json.load(f)

    creds = Credentials(**token_data)

    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        token_data["token"] = creds.token
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f)
        print("GOOGLE TOKEN REFRESHED", flush=True)

    return creds

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
