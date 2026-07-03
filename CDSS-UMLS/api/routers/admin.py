"""Admin endpoints for system management."""


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.services.evaluation.experiments import ExperimentTracker

router = APIRouter()
experiment_tracker = ExperimentTracker()


class ExperimentStatus(BaseModel):
    experiment_id: str
    status: str
    metrics: dict[str, float]
    timestamp: str


@router.get("/experiments")
async def list_experiments():
    """List all experiments."""
    return await experiment_tracker.list_experiments()


@router.get("/experiments/{experiment_id}")
async def get_experiment(experiment_id: str):
    """Get experiment details."""
    experiment = await experiment_tracker.get_experiment(experiment_id)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment


@router.post("/experiments/{experiment_id}/run")
async def run_experiment(experiment_id: str):
    """Run a specific experiment."""
    try:
        result = await experiment_tracker.run_experiment(experiment_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics")
async def get_system_metrics():
    """Get system performance metrics."""
    # TODO: Implement system metrics collection
    return {"api_requests": 0, "avg_response_time": 0.0, "cache_hit_rate": 0.0, "error_rate": 0.0}
