from fastapi import FastAPI

app = FastAPI(title="Parking Analytics", version="0.1.0")

@app.get("/health")
def health():
    return {"service": "parking-analytics", "status": "ok"}

@app.get("/")
def root():
    return {"service": "parking-analytics", "message": "Starter service is running"}
