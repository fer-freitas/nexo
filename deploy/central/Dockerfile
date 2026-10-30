# Coletor central e portal do Nexo. Build na raiz do repositório:
#   docker build -f deploy/central/Dockerfile -t nexo-central .
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 nexo
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[central]"
USER nexo
EXPOSE 8080
CMD ["uvicorn", "nexo.central.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
