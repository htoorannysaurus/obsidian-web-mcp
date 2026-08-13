"""Health endpoint for operational checks."""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .version import __version__


async def health(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
        }
    )


health_routes = [Route("/health", health, methods=["GET"])]
