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

    if not expected or provided != expected:
        return jsonify(ok=False, error="unauthorized"), 401

    data = request.get_json(silent=True) or {}
    return jsonify(ok=True, received=data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
