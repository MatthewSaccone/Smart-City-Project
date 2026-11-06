from fastapi import FastAPI

app = FastAPI(title="Live Traffic Analytics", version="0.1.0")

@app.get("/health")
def health():
    return {"service": "live-traffic", "status": "ok"}

@app.get("/")
def root():
    return {"service": "live-traffic", "message": "Starter service is running"}
