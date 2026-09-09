# Builds the WaterLens network-control helper with NetworkManager CLI access. 2026-09-08 21:16 Europe/Helsinki, Thomas Vikström.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends network-manager && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app /app
CMD ["python", "/app/server.py"]
