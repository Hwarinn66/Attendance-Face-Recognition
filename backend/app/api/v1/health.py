from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DbDep, SettingsDep
from app.biometrics.runtime import BiometricRuntime

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request, db: DbDep, settings: SettingsDep) -> JSONResponse:
    checks: dict[str, object] = {}
    healthy = True
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
        healthy = False
    runtime: BiometricRuntime | None = getattr(request.app.state, "biometrics", None)
    if runtime is None:
        checks["biometrics"] = "not_initialised"
        healthy = False
    else:
        statuses = {s.role: s for s in runtime.provider.statuses()}
        checks["models"] = [s.public() for s in statuses.values()]
        checks["pad_enforced"] = runtime.pad_required
        checks["test_fixture"] = runtime.provider.info.is_test_fixture
        recognition_ok = all(statuses[r].available for r in ("detector", "recognizer") if r in statuses)
        pad_ok = statuses["pad"].available if runtime.pad_required and "pad" in statuses else True
        checks["verification"] = "available" if recognition_ok and pad_ok else "unavailable"
        # Missing models make the service *degraded*, not dead: attendance verification fails closed.
        if settings.is_production and not (recognition_ok and pad_ok):
            healthy = False
    checks["environment"] = settings.app_env
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503
    )
