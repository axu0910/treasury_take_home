from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

from app.api.routes import router
from app.db.database import initialize_database

app = FastAPI(title="Local Label Verification API", version="0.1.0")
app.include_router(router, prefix="/api")
initialize_database()

# A production container build copies the compiled Vite frontend into this
# directory so the API can serve the whole app from a single URL. Local
# development (Vite dev server + `uvicorn --reload`) never populates this
# directory, so behavior there is unchanged.
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


if STATIC_DIR.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")

else:

    @app.get("/")
    def root() -> dict[str, str]:
        return {
            "name": "Local Alcohol Label Verification API",
            "status": "ok",
            "health": "/api/health",
        }
