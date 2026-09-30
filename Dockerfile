# Minimal runtime image for the AegisGate gateway (used by PlatformDemo's docker-compose).
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
CMD ["uvicorn", "aegisgate.gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
