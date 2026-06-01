FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Сначала зависимости — чтобы использовать кэш слоёв Docker.
COPY pyproject.toml README.md ./
COPY src ./src
COPY main.py ./

RUN pip install --no-cache-dir .

EXPOSE 8000

# Render передаёт $PORT; для локального docker run по умолчанию 8000.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
