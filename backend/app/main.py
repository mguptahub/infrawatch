from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from .core.session import session_store, SESSION_COOKIE_NAME, SESSION_TIMEOUT_MINUTES
from .core.database import init_db
from .core.config import settings
from .core.limiter import limiter
from .routers import ec2, eks, rds, docdb, cost, opensearch, mq, elasticache, secrets, ses, lb, iam, dashboard, alerts
from .routers import otp_auth, requests_router, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="InfraWatch API", version="2.0.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    response = await call_next(request)
    if session_id:
        config = session_store.get_session_config(session_id)
        if config:
            session_store.update_session(session_id)
            response.set_cookie(
                key=SESSION_COOKIE_NAME,
                value=session_id,
                httponly=True,
                max_age=SESSION_TIMEOUT_MINUTES * 60,
                samesite="lax",
                secure=settings.cookie_secure,
            )
    return response


app.include_router(ec2.router)
app.include_router(eks.router)
app.include_router(rds.router)
app.include_router(docdb.router)
app.include_router(cost.router)
app.include_router(opensearch.router)
app.include_router(mq.router)
app.include_router(elasticache.router)
app.include_router(secrets.router)
app.include_router(iam.router)
app.include_router(ses.router)
app.include_router(lb.router)
app.include_router(dashboard.router)
app.include_router(alerts.router)

# New access management routers
app.include_router(otp_auth.router)
app.include_router(requests_router.router)
app.include_router(admin.router)


@app.get("/")
async def root():
    return {"message": "InfraWatch API is running"}


@app.get("/api/health")
async def health():
    return {"status": "healthy"}
