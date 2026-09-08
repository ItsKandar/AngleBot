FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Couche de dependances separee : un changement de code ne la reinstalle pas.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py config.yaml ./
COPY anglebot ./anglebot
# Permet `docker compose run --rm anglebot python scripts/smoke_test.py`.
COPY scripts ./scripts

# /app/data est un volume : il doit appartenir a l'utilisateur non-root,
# sinon l'ecriture de state.json echoue au premier cycle de veille.
RUN useradd --create-home --uid 10001 anglebot \
    && mkdir -p /app/data \
    && chown -R anglebot:anglebot /app
USER anglebot

CMD ["python", "-u", "main.py"]
