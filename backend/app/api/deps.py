from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_token
from app.db.session import get_db
from app.models.auth import User
from app.services.auth_service import AuthService, user_permissions

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise credentials_exception from exc
    if payload.get("type") != "access" or not payload.get("sub"):
        raise credentials_exception
    user = AuthService(db).get_user_by_id(payload["sub"])
    if not user or not user.is_active:
        raise credentials_exception
    return user


def require_permissions(*permissions: str) -> Callable[[User], User]:
    def guard(user: User = Depends(get_current_user)) -> User:
        if user.is_superuser or set(permissions).issubset(user_permissions(user)):
            return user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission")

    return guard


def require_roles(*role_names: str) -> Callable[[User], User]:
    # Role-based gate, distinct from require_permissions' flat permission-string
    # check. User.roles is a real relationship (see app/models/auth/models.py),
    # eagerly loaded via lazy="selectin", so this doesn't trigger an extra query.
    # Matches by Role.name, e.g. require_roles("Super Admin"). Superusers bypass
    # the check, same as require_permissions.
    def guard(user: User = Depends(get_current_user)) -> User:
        if user.is_superuser:
            return user
        user_role_names = {role.name for role in user.roles}
        if set(role_names).intersection(user_role_names):
            return user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    return guard


def require_self_employee_or_permission(*permissions: str) -> Callable[..., User]:
    # Allows access if the caller holds one of the given permissions (e.g. HR/managers
    # with "employees:view"), OR if the "employee_id" path parameter refers to the
    # caller's own linked employee record. Used for self-service endpoints — such as
    # viewing your own leave balances/history — that HR/managers also need to reach
    # for arbitrary employees via a broader permission. Read employee_id straight off
    # the request's path params so this stays reusable across routers (leave,
    # attendance, etc.) without needing a matching function-signature param.
    def guard(request: Request, current_user: User = Depends(get_current_user)) -> User:
        if current_user.is_superuser or set(permissions).issubset(user_permissions(current_user)):
            return current_user
        target_id = request.path_params.get("employee_id")
        own_employee = current_user.employee_profile
        if own_employee and target_id and str(own_employee.id) == str(target_id):
            return current_user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission")

    return guard