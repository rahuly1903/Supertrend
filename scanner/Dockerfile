FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

# Railway cron / compose override this, e.g. `python main.py weekly`
CMD ["python", "main.py", "migrate"]
