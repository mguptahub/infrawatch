from fastapi import APIRouter, Depends, Request, Response, HTTPException
from ..models.auth import AuthConfig
from ..core.session import session_store, SESSION_COOKIE_NAME, SESSION_TIMEOUT_MINUTES
from ..core.aws import get_session

router = APIRouter(prefix="/api/auth", tags=["Auth"])

@router.post("/verify")
def verify_auth(config: AuthConfig, response: Response):
    session = get_session(config)
    try:
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        
        session_id = session_store.create_session(config.dict())
        
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_id,
            httponly=True,
            max_age=SESSION_TIMEOUT_MINUTES * 60,
            samesite="lax",
            secure=False,
        )
        
        return {
            "success": True,
            "account": identity["Account"],
            "arn": identity["Arn"],
            "user_id": identity["UserId"]
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

@router.get("/me")
def get_me(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    config_dict = session_store.get_session_config(session_id)
    if not config_dict:
        raise HTTPException(status_code=401, detail="Session expired")
    
    config = AuthConfig(**config_dict)
    session = get_session(config)
    try:
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        return {
            "authenticated": True,
            "account": identity["Account"],
            "arn": identity["Arn"],
            "user_id": identity["UserId"]
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/logout")
def logout(request: Request, response: Response):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        session_store.delete_session(session_id)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"success": True}
