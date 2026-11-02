FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY evaluator evaluator
COPY alembic.ini .
COPY migrations migrations

USER nobody
EXPOSE 8000
CMD ["uvicorn", "evaluator.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
