FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orders_api.py .

# Render injects $PORT; default to 8000 for local runs
ENV PORT=8000
EXPOSE 8000
CMD uvicorn orders_api:app --host 0.0.0.0 --port ${PORT}
