import uvicorn

from backend.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("backend.api:app", host=settings.app_host, port=settings.app_port, reload=False)

