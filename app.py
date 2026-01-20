@app.post("/book_appointment")
def book_appointment():
    expected = os.getenv("X_API_KEY", "")
    provided = request.headers.get("X-API-Key", "")

    # Logs (masqués)
    print("=== BOOK_APPOINTMENT HIT ===", flush=True)
    print("Content-Type:", request.headers.get("Content-Type"), flush=True)
    print("X-API-Key provided len:", len(provided), flush=True)
    print("X-API-Key expected len:", len(expected), flush=True)

    raw = request.get_data(as_text=True)
    print("RAW BODY:", raw, flush=True)

    data = request.get_json(silent=True)
    print("JSON PARSED:", data, flush=True)

    if not expected or provided != expected:
        print("AUTH FAIL", flush=True)
        return jsonify(ok=False, error="unauthorized"), 401

    if not data:
        print("NO JSON / BAD JSON", flush=True)
        return jsonify(ok=False, error="bad_json"), 400

    return jsonify(ok=True, received=data), 200
