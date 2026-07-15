from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.salary_assignment_agent.agent import SalaryAssignmentAgent
from app.agents.salary_assignment_agent.services import SalaryAssignmentService
from app.agents.attendance_agent.tools import attendance_summary
from app.agents.employee_agent.tools import employee_display_name
from app.api.deps import get_current_user, require_permissions
from app.db.session import get_db
from app.models.auth import User
from app.models.employee import Employee


router = APIRouter()


class SalaryCommandRequest(BaseModel):
    command: str = Field(..., min_length=1)


def _structure_active(structure: Any) -> bool:
    # NEW: an assignment can point at a SalaryStructure that has since been
    # soft-deleted or deactivated (payroll.py's DELETE /structures/{id} does
    # not currently block on active assignments — flagged there, not fixed
    # here since this file has no visibility into the assignment schema
    # needed to add that guard). Surface it here instead so a breakup number
    # is never shown to look authoritative when it's actually computed
    # against a dead structure.
    if not structure:
        return False
    return bool(getattr(structure, "active", True)) and getattr(structure, "deleted_at", None) is None


@router.post("/command", dependencies=[Depends(require_permissions("payroll:view"))])
def salary_assignment_command(payload: SalaryCommandRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    # DECISION (resolved, not a bug): action is hardcoded to "inspect" regardless
    # of what the command text says. This is intentional, not a gap to close.
    # Salary mutations (assign/revise) already have a governed path: the
    # coordinator's CRITICAL_ACTION_KEYWORDS routes "update salary"/"change
    # salary" through this same SalaryAssignmentAgent's revise/activate actions
    # via /agent-command, gated behind mandatory human approval
    # (ApprovalEngineService). A second REST route that could mutate salary
    # directly would bypass that approval step entirely — not acceptable for
    # data this sensitive. This endpoint stays read-only by design; it exists
    # only to let the UI inspect current salary/assignment state without
    # spinning up a full coordinator workflow for a simple lookup.
    return SalaryAssignmentAgent(db).execute(action="inspect", command=payload.command, user_id=current_user.id, workflow_id="salary-assignment-api")



@router.get("/employees/{employee_id}", dependencies=[Depends(require_permissions("payroll:view"))])
def employee_salary(employee_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    service = SalaryAssignmentService(db)
    # FIX: previously fetched the employee only inside the "no assignment"
    # branch, so a request for a nonexistent/invalid employee_id fell
    # through to the same 200 response as "employee exists but has no
    # salary assigned yet" — indistinguishable from the outside. The sibling
    # payroll-impact endpoint below already 404s on a missing employee;
    # this one now matches that precedent.
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    assignment = service.active_assignment(employee_id)
    if not assignment:
        if employee.current_salary is None:
            return {"current": None, "breakup": None, "history": service.assignment_history(employee_id), "structure_assigned": False}
        gross = float(employee.current_salary)
        return {
            "current": {
                "id": None,
                "employee_id": str(employee.id),
                "employee_name": employee_display_name(employee),
                "salary_structure_id": None,
                "salary_structure": "Salary structure not assigned",
                "gross_salary": gross,
                "gross_salary_display": f"₹{gross:,.0f}",
                "effective_from": None,
                "effective_to": None,
                "status": "UNSTRUCTURED",
            },
            "breakup": None,
            "history": service.assignment_history(employee_id),
            "structure_assigned": False,
        }
    structure = assignment.salary_structure
    return {
        "current": service.assignment_summary(assignment),
        "breakup": service.calculate_breakup(structure, assignment.gross_salary),
        "history": service.assignment_history(employee_id),
        "structure_assigned": True,
        # NEW: see _structure_active note above.
        "structure_active": _structure_active(structure),
    }


@router.get("/employees/{employee_id}/payroll-impact", dependencies=[Depends(require_permissions("payroll:view"))])
def employee_payroll_impact(
    employee_id: UUID,
    # FIX: `Query(default=date.today().month, ...)` evaluates date.today()
    # exactly once, at module import time — so the "default" month/year
    # would silently freeze to whatever day the server last restarted on,
    # not today. Default to None and resolve the actual date inside the
    # request instead.
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2000, le=2200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    today = date.today()
    month = month if month is not None else today.month
    year = year if year is not None else today.year

    service = SalaryAssignmentService(db)
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    attendance = attendance_summary(db, employee=employee, month=month, year=year)
    assignment = service.active_assignment(employee_id)
    if not assignment:
        return {
            "employee_id": str(employee.id),
            "employee_name": attendance["employee_name"],
            "employment_type": str(employee.employment_type),
            "month": month,
            "year": year,
            "attendance": attendance,
            "salary_ready": False,
            "blocking_issues": ["Active salary assignment is missing"],
        }
    structure = assignment.salary_structure
    breakup = service.calculate_breakup(structure, assignment.gross_salary)
    gross = float(assignment.gross_salary)
    working_days = float(attendance["working_days"] or 0)
    lop_days = float(attendance["lop_days"] or 0)
    lop_deduction = round((gross / working_days) * lop_days, 2) if working_days else 0
    other_deductions = float(breakup["deductions"])
    estimated_net = max(0, round(gross - other_deductions - lop_deduction, 2))
    blocking_issues = []
    # NEW: see _structure_active note above — the deleted/deactivated case.
    if not _structure_active(structure):
        blocking_issues.append("Assigned salary structure has been deactivated or deleted")
    if float(attendance["payable_days"]) < working_days and month == date.today().month and year == date.today().year:
        blocking_issues.append("Current-month attendance is still in progress")
    return {
        "employee_id": str(employee.id),
        "employee_name": attendance["employee_name"],
        "employment_type": str(employee.employment_type),
        "month": month,
        "year": year,
        "attendance": attendance,
        # FIX: was hardcoded True here regardless of blocking_issues, so a
        # response could list a blocking issue and simultaneously claim
        # salary_ready: True. Now derived from the same list it's meant to
        # reflect.
        "salary_ready": not blocking_issues,
        "gross_salary": gross,
        "gross_salary_display": breakup["gross_salary_display"],
        "other_deductions": other_deductions,
        "other_deductions_display": breakup["deductions_display"],
        "lop_deduction": lop_deduction,
        "lop_deduction_display": f"₹{lop_deduction:,.0f}",
        "estimated_net": estimated_net,
        "estimated_net_display": f"₹{estimated_net:,.0f}",
        "blocking_issues": blocking_issues,
    }


@router.get("/history", dependencies=[Depends(require_permissions("payroll:view"))])
def salary_history(employee: str = Query(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    service = SalaryAssignmentService(db)
    record = service.find_employee(employee)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return {
        "employee_id": str(record.id),
        # FIX: was echoing back the raw query string the caller typed
        # instead of the resolved employee's actual name. Harmless when
        # find_employee does an exact match, misleading the moment it does
        # any fuzzy/partial matching — the response would claim a name the
        # employee doesn't have.
        "employee_name": employee_display_name(record),
        "history": service.assignment_history(record.id),
    }


@router.get("/pending", dependencies=[Depends(require_permissions("payroll:view"))])
def pending_salary_assignments(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    service = SalaryAssignmentService(db)
    return [service.assignment_summary(row) for row in service.pending_assignments()]