FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot.py bot_instance.py config.py db.py keyboards.py scheduler.py state.py ./
COPY services/ ./services/
COPY handlers/ ./handlers/

RUN mkdir -p /app/data
VOLUME ["/app/data"]

CMD ["python", "bot.py"]
