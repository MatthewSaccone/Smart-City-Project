from fastapi import FastAPI

app = FastAPI(title="Weather and Air Quality Analytics", version="0.1.0")

@app.get("/health")
def health():
    return {"service": "weather-analytics", "status": "ok"}

@app.get("/")
def root():
    return {"service": "weather-analytics", "message": "Starter service is running"}
