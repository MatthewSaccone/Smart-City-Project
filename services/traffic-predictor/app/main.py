from fastapi import FastAPI

app = FastAPI(title="Traffic Predictor", version="0.1.0")

@app.get("/health")
def health():
    return {"service": "traffic-predictor", "status": "ok"}

@app.get("/")
def root():
    return {"service": "traffic-predictor", "message": "Starter service is running"}
