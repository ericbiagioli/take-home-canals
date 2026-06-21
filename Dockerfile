FROM python:3.12-slim

WORKDIR /app

# PYTHONUNBUFFERED: flush stdout/stderr immediately so logs show up in
# `docker logs` as they happen instead of waiting on Python's buffering.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user rather than root.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

# In the current approach, schema setup does not happen at app boot (see db/
# and README), and each worker opens its own psycopg2 connection pool in
# init_db_pool() -- which is NOT fork-safe to share.
#
# Preloading would create the pool once in the master and fork its live, open
# TCP connections into every worker, which is a real footgun (workers would
# corrupt each other's protocol state on the same socket). Letting each worker
# import the app and build its own pool independently after fork is the
# standard, correct pattern here.
#
# In other words: not using "--preload" is intentional here.
#
# >> PLEASE DON'T ADD --preload BECAUSE THE CONNECTION POOL IS NOT FORK-SAFE <<

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "30", "run:app"]
