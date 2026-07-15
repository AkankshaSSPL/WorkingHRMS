from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.auth_middleware import AuthContextMiddleware
from app.core.config import settings
from app.core.logging import configure_logging

# Known placeholder values seen in this repo's config.py defaults / .env.example
# over time. Kept as an explicit set (rather than re-importing from config.py)
# so this check still works even if config.py's defaults change shape later.
# NOTE: this is a denylist of *known* placeholders, not a substitute for real
# strength validation — the minimum-length check below is what actually
# catches placeholders we haven't seen yet.
_KNOWN_JWT_SECRET_PLACEHOLDERS = {
    "change-me-before-production",
    "replace-with-a-long-random-secret",
    "secret",
    "changeme",
}
_KNOWN_ADMIN_PASSWORD_PLACEHOLDERS = {
    "ChangeMe123!",
    "changeme",
    "password",
    "admin123",
}
_MIN_JWT_SECRET_LENGTH = 32  # a real secrets.token_urlsafe() value clears this easily


def _verify_production_secrets() -> None:
    """Refuse to start in production with secrets that were never actually
    configured. Booting quietly on placeholder defaults would mean either
    every token is signed with a publicly-known string, or the seeded
    superuser account has a publicly-known password — a silent, total auth
    bypass rather than a loud config error.

    Uses two layers so a *new*, previously-unseen placeholder still gets
    caught:
    1. Exact match against known placeholder strings we've encountered.
    2. A minimum-length bar on the JWT secret, since any real random secret
       will comfortably clear it while short human-typed placeholders won't.
    """
    if settings.environment != "production":
        return

    problems: list[str] = []

    jwt_secret = settings.jwt_secret_key
    if jwt_secret in _KNOWN_JWT_SECRET_PLACEHOLDERS:
        problems.append(
            "JWT_SECRET_KEY is still set to a known placeholder value."
        )
    elif len(jwt_secret) < _MIN_JWT_SECRET_LENGTH:
        problems.append(
            f"JWT_SECRET_KEY is only {len(jwt_secret)} characters — too short to be "
            f"a real random secret (expected at least {_MIN_JWT_SECRET_LENGTH}). "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
        )

    if settings.admin_password in _KNOWN_ADMIN_PASSWORD_PLACEHOLDERS:
        problems.append(
            "ADMIN_PASSWORD is still set to a known placeholder value "
            "(the seeded superuser account would have a publicly-known password)."
        )

    if problems:
        raise RuntimeError(
            "Refusing to start with ENVIRONMENT=production while default/weak secrets are "
            "still in place:\n- " + "\n- ".join(problems) +
            "\nSet real, random values for these in the environment before starting the app in production."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    _verify_production_secrets()
    yield


app = FastAPI(
    title=settings.project_name,
    version=settings.app_version,
    description="Enterprise-grade multi-agent HRMS foundation.",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthContextMiddleware)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    return {"service": settings.project_name, "status": "ready"}