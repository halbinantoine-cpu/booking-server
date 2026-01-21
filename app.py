import os
import json
from flask import Flask, request, jsonify, redirect
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta

app = Flask(__name__)

# Config Google OAuth
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = "https://booking-server-u1ep.onrender.com/oauth/callback"

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = "/tmp/google_token.json"  # Fichier temporaire Render

# === HEALTH CHECK ===
@app.route("/health", methods=["GET"])
def health():
    return jsonify(ok=True, service="booking-server"), 200

# === OAUTH : Démarrer l'auth ===
@app.route("/oauth/start", methods=["GET"])
def oauth_start():
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
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return redirect(auth_url)

# === OAUTH : Callback après autorisation ===
@app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
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
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    # Sauvegarder le token dans un fichier
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)
    
    print("GOOGLE TOKEN SAVED", flush=True)

    return jsonify(ok=True, message="Authentification réussie ! Tu peux fermer cette page."), 200

# === Charger le token Google ===
def load_google_credentials():
    if not os.path.exists(TOKEN_FILE):
        return None
    
    with open(TOKEN_FILE, "r") as f:
        token_data = json.load(f)
    
    creds = Credentials(**token_data)
    
    # Rafraîchir le token si expiré
    if creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        
        # Sauvegarder le nouveau token
        token_data["token"] = creds.token
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f)
        
        print("GOOGLE TOKEN REFRESHED", flush=True)
    
    return creds

# === BOOK APPOINTMENT (avec création Google Calendar) ===
@app.route("/book_appointment", methods=["POST"])
def book_appointment():
    expected = os.getenv("X_API_KEY", "")
    provided = request.headers.get("X-API-Key", "")

    data = request.get_json(silent=True) or {}

    # Logs safe
    print("BOOK_APPOINTMENT HIT", flush=True)
    print(f"has_expected_key: {bool(expected)} provided_len: {len(provided)}", flush=True)
    print(f"content_type: {request.headers.get('Content-Type')}", flush=True)
    print(f"fields: {list(data.keys())}", flush=True)

    # Vérif auth API
    if not expected or provided != expected:
        print("AUTH FAIL", flush=True)
        return jsonify(ok=False, error="unauthorized"), 401

    if not data:
        print("NO JSON", flush=True)
        return jsonify(ok=False, error="bad_json"), 400

    # Charger les credentials Google
    creds = load_google_credentials()
    if not creds:
        print("NO GOOGLE AUTH", flush=True)
        return jsonify(ok=False, error="not_authenticated", auth_url=f"{REDIRECT_URI.rsplit('/', 1)[0]}/oauth/start"), 401

    # Créer l'événement Google Calendar
    try:
        service = build("calendar", "v3", credentials=creds)

        # Calculer l'heure de fin (+1h par défaut)
        start_time = data.get("start_time")
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = start_dt + timedelta(hours=1)

        event = {
            "summary": f"RDV {data.get('service', 'Prestation')} - {data.get('customer_name', 'Client')}",
            "description": f"Client: {data.get('customer_name')}\nTéléphone: {data.get('phone', 'Non fourni')}\nNotes: {data.get('notes', 'Aucune')}",
            "start": {
                "dateTime": start_time,
                "timeZone": "Europe/Paris",
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": "Europe/Paris",
            },
        }

        created_event = service.events().insert(calendarId="primary", body=event).execute()

        print(f"EVENT CREATED: {created_event.get('id')}", flush=True)

        return jsonify(
            ok=True,
            message="Rendez-vous créé avec succès",
            event_id=created_event.get("id"),
            event_link=created_event.get("htmlLink"),
        ), 200

    except Exception as e:
        print(f"CALENDAR ERROR: {str(e)}", flush=True)
        return jsonify(ok=False, error="calendar_failed", details=str(e)), 500

# === LANCER LE SERVEUR ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
