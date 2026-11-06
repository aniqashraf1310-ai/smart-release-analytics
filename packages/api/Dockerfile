# Build context: repository root  (docker compose handles this)
FROM python:3.11-slim

WORKDIR /app
COPY packages/ml/requirements.txt packages/ml/requirements.txt
COPY packages/api/requirements.txt packages/api/requirements.txt
RUN pip install --no-cache-dir -r packages/api/requirements.txt

COPY packages/ml packages/ml
COPY packages/api packages/api

# Train the risk model at build time so the API starts with a model loaded.
RUN cd packages/ml && python train.py

WORKDIR /app/packages/api
EXPOSE 5000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000"]
