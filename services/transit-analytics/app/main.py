from fastapi import FastAPI

app = FastAPI(title="Public Transit Analytics", version="0.1.0")

@app.get("/health")
def health():
    return {"service": "transit-analytics", "status": "ok"}

@app.get("/")
def root():
    return {"service": "transit-analytics", "message": "Starter service is running"}
