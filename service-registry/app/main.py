from fastapi import FastAPI

app = FastAPI(title="Smart City Service Registry", version="0.1.0")

@app.get("/health")
def health():
    return {"service": "service-registry", "status": "ok"}

@app.get("/")
def root():
    return {"service": "service-registry", "status": "scaffold",
            "note": "Registration and discovery endpoints are not implemented yet."}
