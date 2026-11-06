from fastapi import FastAPI

app = FastAPI(title="Gasoline Price Analytics", version="0.1.0")

@app.get("/health")
def health():
    return {"service": "gasoline-analytics", "status": "ok"}

@app.get("/")
def root():
    return {"service": "gasoline-analytics", "message": "Starter service is running"}
