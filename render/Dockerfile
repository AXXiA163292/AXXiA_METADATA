FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
CMD ["sh","-c","gunicorn -b 0.0.0.0:${PORT:-8080} -w 1 --threads 4 --timeout 120 app:app"]