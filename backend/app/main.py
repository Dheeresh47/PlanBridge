from fastapi import FastAPI

app = FastAPI(
    title="PlanBridge API",
    description="Project progress capture and schedule-linking API.",
    version="0.1.0",
)


@app.get("/health", tags=["Health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}