from fastapi import FastAPI

app = FastAPI(title="Construction and Incident Analytics", version="0.1.0")

@app.get("/health")
def health():
    return {"service": "construction-incidents", "status": "ok"}

@app.get("/")
def root():
    return {"service": "construction-incidents", "message": "Starter service is running"}
