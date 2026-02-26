from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .core.session import session_store, SESSION_COOKIE_NAME, SESSION_TIMEOUT_MINUTES
from .core.database import init_db
from .routers import auth, ec2, eks, rds, cost, alarms, opensearch, mq, elasticache, secrets, ses, lb
from .routers import otp_auth, requests_router, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AWS Monitor API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
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
                secure=False,
            )
    return response


# Existing AWS routers (still work for direct key-based access during transition)
app.include_router(auth.router)
app.include_router(ec2.router)
app.include_router(eks.router)
app.include_router(rds.router)
app.include_router(cost.router)
app.include_router(alarms.router)
app.include_router(opensearch.router)
app.include_router(mq.router)
app.include_router(elasticache.router)
app.include_router(secrets.router)
app.include_router(ses.router)
app.include_router(lb.router)

# New access management routers
app.include_router(otp_auth.router)
app.include_router(requests_router.router)
app.include_router(admin.router)


@app.get("/")
async def root():
    return {"message": "AWS Monitor API is running"}


@app.get("/api/health")
async def health():
    return {"status": "healthy"}
