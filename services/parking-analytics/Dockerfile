FROM python:3.12-slim

WORKDIR /app

COPY parking-analytics/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY parking-analytics/app/ ./app/

COPY shared/ ./shared/

ENV PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
