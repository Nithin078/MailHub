from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import read_access_token

bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    user_id = read_access_token(credentials.credentials)
    if not user_id:
        raise HTTPException(status_code=401, detail="Sign in required")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user
