FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
EXPOSE 8080
CMD ["sh","-c","gunicorn -b 0.0.0.0:${PORT} --workers 1 --threads 2 --timeout 210 --access-logfile - --error-logfile - app:app"]

