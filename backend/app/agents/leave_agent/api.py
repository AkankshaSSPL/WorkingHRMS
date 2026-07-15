from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_permissions, require_self_employee_or_permission
from app.db.session import get_db
from app.models.auth import User
from app.models.employee import Employee
from app.agents.employee_agent.tools import get_employee_by_id
from app.agents.leave_agent.tools import (
    create_or_update_leave_type,
    create_leave_request,
    ensure_default_leave_types,
    leave_request_payload,
    leave_balances,
    leave_history,
    pending_leave_requests,
    team_leave_calendar,
)
from app.models.employee import LeaveType
from app.services.auth_service import user_permissions

router = APIRouter()


class LeaveRequestCreate(BaseModel):
    employee_id: UUID
    leave_type: str
    start_date: date
    end_date: date
    reason: str | None = None


@router.get("/pending", dependencies=[Depends(require_permissions("approvals:view"))])
def list_pending_leave_requests(db: Session = Depends(get_db)):
    return pending_leave_requests(db)


@router.get("/calendar", dependencies=[Depends(require_permissions("employees:view"))])
def leave_calendar(db: Session = Depends(get_db)):
    return team_leave_calendar(db)


@router.get("/policies", dependencies=[Depends(require_permissions("leave:view"))])
def leave_policies(db: Session = Depends(get_db)):
    ensure_default_leave_types(db)
    db.commit()
    policies = db.scalars(select(LeaveType).where(LeaveType.deleted_at.is_(None), LeaveType.active.is_(True)).order_by(LeaveType.name)).all()
    return [
        {
            "id": str(policy.id),
            "name": policy.name,
            "code": policy.code,
            "category": str(policy.category),
            "annual_allocation": float(policy.annual_allocation or 0),
            "requires_approval": policy.requires_approval,
            "affects_payroll": policy.affects_payroll,
        }
        for policy in policies
    ]


@router.post("/requests", dependencies=[Depends(require_permissions("leave:view"))])
def apply_leave(
    payload: LeaveRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    employee = get_employee_by_id(db, payload.employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    # Previously unchecked: any authenticated user with "leave:view" could submit a
    # leave request for any employee_id, since the UI's employee picker and the API
    # both accepted an arbitrary id with no ownership check. Now restricted to the
    # requester's own employee record unless they hold "employees:view" (HR/managers
    # applying leave on someone else's behalf).
    if not current_user.is_superuser and "employees:view" not in user_permissions(current_user):
        own_employee = current_user.employee_profile
        if not own_employee or own_employee.id != employee.id:
            raise HTTPException(status_code=403, detail="You can only submit leave requests for yourself.")
    try:
        request = create_leave_request(
            db,
            employee=employee,
            leave_type_name=payload.leave_type,
            start_date=payload.start_date,
            end_date=payload.end_date,
            reason=payload.reason,
            requested_by=current_user.id,
        )
        db.commit()
        return leave_request_payload(request, employee)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/employees/{employee_id}/balances", dependencies=[Depends(require_self_employee_or_permission("employees:view"))])
def employee_leave_balances(employee_id: str, db: Session = Depends(get_db)):
    employee = get_employee_by_id(db, employee_id)
    if not employee:
        return []
    return leave_balances(db, employee=employee)


@router.get("/employees/{employee_id}/history", dependencies=[Depends(require_self_employee_or_permission("employees:view"))])
def employee_leave_history(employee_id: str, db: Session = Depends(get_db)):
    employee = get_employee_by_id(db, employee_id)
    if not employee:
        return []
    return leave_history(db, employee=employee)


@router.post("/policies", dependencies=[Depends(require_permissions("approvals:manage"))])
def create_policy(payload: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    command = str(payload.get("command") or payload.get("name") or "Create leave policy")
    policy = create_or_update_leave_type(db, command)
    db.commit()
    return policy