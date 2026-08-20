# Python
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IRONBOUND_HOST=0.0.0.0 \
    IRONBOUND_PORT=5000 \
    IRONBOUND_SERVER=waitress \
    IRONBOUND_DB=/data/ironbound.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static ./static
COPY templates ./templates

RUN mkdir -p /data

EXPOSE 5000

# IRONBOUND_SECRET_KEY: set a fixed value via --env/-e at run time so sessions
# survive restarts. Data lives in /data (mount a volume there).
CMD ["python", "app.py"]