"""
UMLS Clinical Decision Support System - FastAPI Application
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.config import settings
from api.db import init_db
from api.routers import healthcheck, terminology, inference_v1, inference_v2, admin

app = FastAPI(
    title="UMLS CDSS API",
    description="Clinical Decision Support System powered by UMLS and LLMs",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(healthcheck.router, prefix="/health", tags=["health"])
app.include_router(terminology.router, prefix="/api/v1/terminology", tags=["terminology"])
app.include_router(inference_v1.router, prefix="/api/v1/inference", tags=["inference"])
app.include_router(inference_v2.router, prefix="/api/v2/inference", tags=["inference"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])


@app.on_event("startup")
async def startup_event():
    """Initialize database connections on startup"""
    await init_db()


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

