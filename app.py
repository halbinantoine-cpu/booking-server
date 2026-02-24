import os
import json
import re
from flask import Flask, request, jsonify, redirect
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta

app = Flask(__name__)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = "https://booking-server-u1ep.onrender.com/oauth/callback"

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = "/tmp/google_token.json"

def normalize_string(s):
    if not s:
        return ""
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e")
    s = s.replace("à", "a").replace("â", "a")
    s = s.replace("ô", "o").replace("ö", "o")
    s = s.replace("ù", "u").replace("û", "u")
    s = s.replace("ç", "c")
    s = s.lower().replace(" ", "").replace("_", "").replace("-", "")
    return s

def get_field(data, *possible_keys, default=None):
    for key in possible_keys:
        normalized_key = normalize_string(key)
        for data_key, value in data.items():
            if normalize_string(data_key) == normalized_key:
                if isinstance(value, str):
                    value = value.strip()
                return value if value else default
    return default

@app.route("/health", methods=["GET"])
def health():
    return jsonify(ok=True, service="booking-server"), 200

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

    data = request.get_json(silent=True) or {}

    print("=" * 50, flush=True)
    print("BOOK_APPOINTMENT HIT", flush=True)
    print(f"has_expected_key: {bool(expected)} provided_len: {len(provided)}", flush=True)
    print(f"content_type: {request.headers.get('Content-Type')}", flush=True)
    print(f"raw_fields: {list(data.keys())}", flush=True)
    print(f"raw_data: {data}", flush=True)
    print("=" * 50, flush=True)

    if not expected or provided != expected:
        print("AUTH FAIL", flush=True)
        return jsonify(ok=False, error="unauthorized"), 401

    if not data:
        print("NO JSON", flush=True)
        return jsonify(ok=False, error="bad_json"), 400

    creds = load_google_credentials()
    if not creds:
        print("NO GOOGLE AUTH", flush=True)
        return jsonify(ok=False, error="not_authenticated", auth_url=f"{REDIRECT_URI.rsplit('/', 1)[0]}/oauth/start"), 401

    customer_name = get_field(
        data,
        "customer_name", "customername", "nom", "name", "client", "prenom", "fullname",
        default="Client"
    )
    
    service_type = get_field(
        data,
        "service", "prestation", "type", "service_type", "servicetype",
        default="Prestation"
    )
    
    phone = get_field(
        data,
        "phone", "telephone", "tel", "numero", "number", "mobile", "portable",
        default="Non fourni"
    )
    
    notes = get_field(
        data,
        "notes", "remarques", "commentaire", "comment", "info", "informations",
        default=""
    )
    
    start_time = get_field(
        data,
        "start_time", "starttime", "date", "datetime", "start", "heure", "horaire"
    )

    print(f"PARSED FIELDS:", flush=True)
    print(f"  customer_name: {customer_name}", flush=True)
    print(f"  service: {service_type}", flush=True)
    print(f"  phone: {phone}", flush=True)
    print(f"  notes: {notes}", flush=True)
    print(f"  start_time: {start_time}", flush=True)

    if not start_time:
        print("ERROR: NO START_TIME", flush=True)
        return jsonify(ok=False, error="missing_start_time"), 400

    try:
        service = build("calendar", "v3", credentials=creds)

        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except:
            try:
                start_dt = datetime.fromisoformat(start_time)
            except:
                print(f"ERROR: Invalid date format: {start_time}", flush=True)
                return jsonify(ok=False, error="invalid_date_format"), 400

        end_dt = start_dt + timedelta(hours=1)

        print(f"CHECKING AVAILABILITY: {start_dt.isoformat()} - {end_dt.isoformat()}", flush=True)
        
        events_result = service.events().list(
            calendarId="primary",
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        
        existing_events = events_result.get("items", [])
        num_existing = len(existing_events)
        
        print(f"EXISTING EVENTS IN SLOT: {num_existing}", flush=True)
        
        MAX_CONCURRENT_APPOINTMENTS = 3
        
        if num_existing >= MAX_CONCURRENT_APPOINTMENTS:
            print(f"SLOT FULL: {num_existing}/{MAX_CONCURRENT_APPOINTMENTS}", flush=True)
            existing_summaries = [e.get("summary", "RDV") for e in existing_events]
            print(f"Existing: {', '.join(existing_summaries)}", flush=True)
            
            return jsonify(
                ok=False,
                error="slot_full",
                message=f"Ce créneau est complet ({num_existing} rendez-vous déjà pris). Nous avons 3 coiffeurs disponibles.",
                num_existing=num_existing,
                max_capacity=MAX_CONCURRENT_APPOINTMENTS,
                requested_time=start_time
            ), 409

        print(f"SLOT AVAILABLE: {num_existing}/{MAX_CONCURRENT_APPOINTMENTS}", flush=True)

        description_parts = [f"Client: {customer_name}"]
        if phone and phone != "Non fourni":
            description_parts.append(f"Téléphone: {phone}")
        if notes:
            description_parts.append(f"Notes: {notes}")
        
        description = "\n".join(description_parts)

        event = {
            "summary": f"RDV {service_type} - {customer_name}",
            "description": description,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": "Europe/Paris",
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": "Europe/Paris",
            },
        }

        print(f"CREATING EVENT: {event['summary']}", flush=True)

        created_event = service.events().insert(calendarId="primary", body=event).execute()

        event_id = created_event.get("id")
        event_link = created_event.get("htmlLink")

        print(f"EVENT CREATED: {event_id}", flush=True)
        print(f"Link: {event_link}", flush=True)

        return jsonify(
            ok=True,
            message="Rendez-vous créé avec succès",
            event_id=event_id,
            event_link=event_link,
        ), 200

    except Exception as e:
        print(f"CALENDAR ERROR: {str(e)}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
        return jsonify(ok=False, error="calendar_failed", details=str(e)), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
