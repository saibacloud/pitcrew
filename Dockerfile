FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Persistent data dirs — DB lives at /app/data (mounted volume)
# Uploads live at /app/static/uploads (mounted volume)
RUN mkdir -p /app/data /app/static/uploads/manuals /app/static/uploads/views /app/static/uploads/originals

ENV PYTHONUNBUFFERED=1
# Override in docker-compose / swarm service to point at the volume
ENV PITCREW_DB_PATH=/app/data/pitcrew.db

EXPOSE 8000

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
