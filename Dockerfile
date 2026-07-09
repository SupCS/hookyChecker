FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 hooky
RUN chown -R hooky:hooky /app
USER hooky

CMD ["hooky-checker", "serve", "--host", "0.0.0.0"]
