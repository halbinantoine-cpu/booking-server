import os
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify(ok=True, service="booking-server")

@app.post("/book_appointment")
def book_appointment():
    expected = os.getenv("X_API_KEY", "")
    provided = request.headers.get("X-API-Key", "")

    data = request.get_json(silent=True) or {}

    # Logs safe (ne pas afficher de PII)
    print("BOOK_APPOINTMENT HIT", flush=True)
    print("has_expected_key:", bool(expected), "provided_len:", len(provided), flush=True)
    print("content_type:", request.headers.get("Content-Type"), flush=True)
    print("fields:", list(data.keys()), flush=True)

    if not expected or provided != expected:
        print("AUTH FAIL", flush=True)
        return jsonify(ok=False, error="unauthorized"), 401

    if not data:
        print("NO JSON", flush=True)
        return jsonify(ok=False, error="bad_json"), 400

    return jsonify(ok=True, received=data), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
