import os

from app import create_app

app = create_app()

if __name__ == "__main__":

    port = int(os.environ.get("PORT", "8000"))

    # threaded=True so the dev server can actually handle overlapping
    # requests (matters for exercising the concurrency-safety of the
    # inventory reservation locally). Flask's built-in server is still only
    # for local dev -- run behind gunicorn with multiple workers (see
    # README/Dockerfile) for anything resembling production traffic.

    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1", threaded=True)
