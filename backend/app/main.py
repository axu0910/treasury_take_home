from fastapi import FastAPI
from fastapi.responses import Response

from app.api.routes import router
from app.db.database import initialize_database

app = FastAPI(title="Local Label Verification API", version="0.1.0")
app.include_router(router, prefix="/api")
initialize_database()


@app.get("/")
def root() -> dict[str, str]:
	return {
		"name": "Local Alcohol Label Verification API",
		"status": "ok",
		"health": "/api/health",
	}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
	return Response(status_code=204)
