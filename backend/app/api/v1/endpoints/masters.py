from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.payroll_agent.tools import normalize_code
from app.api.deps import get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.employee import Department, Designation, Employee, LeaveBalance, LeaveRequest, LeaveType
from app.models.lookup import LookupValue

router = APIRouter()


class MasterPayload(BaseModel):
    name: str | None = None
    title: str | None = None
    code: str | None = None
    description: str | None = None
    level: str | None = None
    parent_department_id: UUID | None = None
    category: str | None = None
    label: str | None = None
    sort_order: int = 0
    active: bool = True
    annual_allocation: float = 0
    carry_forward_allowed: bool = False
    requires_approval: bool = True
    affects_payroll: bool = False


def _record_payload(record: Any, master_type: str) -> dict[str, Any]:
    if master_type == "departments":
        return {
            "id": str(record.id),
            "name": record.name,
            "code": record.code,
            "description": record.description,
            "parent_department_id": str(record.parent_department_id) if record.parent_department_id else None,
            "active": record.active and record.deleted_at is None,
        }
    if master_type == "designations":
        return {"id": str(record.id), "name": record.title, "code": record.code, "description": record.description, "level": record.level, "active": record.deleted_at is None}
    if master_type == "leave_types":
        return {"id": str(record.id), "name": record.name, "code": record.code, "category": str(record.category), "annual_allocation": float(record.annual_allocation or 0), "carry_forward_allowed": record.carry_forward_allowed, "requires_approval": record.requires_approval, "affects_payroll": record.affects_payroll, "active": record.active and record.deleted_at is None}
    return {"id": str(record.id), "name": record.label, "label": record.label, "code": record.code, "category": record.category, "sort_order": record.sort_order, "active": record.active and record.deleted_at is None}


def _employee_display_name(employee: Employee) -> str:
    name = " ".join(part for part in (employee.first_name, employee.last_name) if part).strip()
    return name or employee.employee_code or str(employee.id)


# NEW: previously delete_master soft-deleted departments/designations/leave
# types with no check for whether any employee (or, for leave types, any
# leave request/balance) currently references them — the same orphaning bug
# fixed for salary structures in payroll.py, unguarded here for three more
# master types. These three helpers mirror find_component_structures'
# pattern from payroll.py: query for non-deleted referencing rows.
def find_department_employees(db: Session, department_id: UUID) -> list[Employee]:
    return list(
        db.scalars(
            select(Employee).where(
                Employee.deleted_at.is_(None),
                Employee.department_id == department_id,
            )
        ).all()
    )


def find_designation_employees(db: Session, designation_id: UUID) -> list[Employee]:
    return list(
        db.scalars(
            select(Employee).where(
                Employee.deleted_at.is_(None),
                Employee.designation_id == designation_id,
            )
        ).all()
    )


def find_leave_type_usage_count(db: Session, leave_type_id: UUID) -> int:
    # NOTE: LeaveRequest/LeaveBalance also carry a denormalized `leave_type`
    # string column alongside the nullable `leave_type_id` FK. This only
    # checks the FK. A row with leave_type_id left NULL but referencing this
    # type only via the string field would not be counted here — that is an
    # existing gap in how those rows get written, not something this guard
    # can safely paper over without risking false negatives on other data.
    request_count = db.scalar(
        select(func.count(LeaveRequest.id)).where(
            LeaveRequest.deleted_at.is_(None),
            LeaveRequest.leave_type_id == leave_type_id,
        )
    ) or 0
    balance_count = db.scalar(
        select(func.count(LeaveBalance.id)).where(
            LeaveBalance.deleted_at.is_(None),
            LeaveBalance.leave_type_id == leave_type_id,
        )
    ) or 0
    return int(request_count) + int(balance_count)


# NEW: parent_department_id was accepted by MasterPayload but silently
# dropped by both create_master and update_master, so the field existed on
# the model with no way to actually set it through the API. This validates
# it exists (and isn't soft-deleted) and blocks a department being set as
# its own parent, which would otherwise create a one-node cycle.
def _validate_parent_department(db: Session, parent_id: UUID | None, own_id: UUID | None) -> UUID | None:
    if parent_id is None:
        return None
    if own_id is not None and parent_id == own_id:
        raise HTTPException(status_code=400, detail="A department cannot be its own parent.")
    parent = db.get(Department, parent_id)
    if not parent or parent.deleted_at is not None:
        raise HTTPException(status_code=400, detail="Parent department not found.")
    return parent_id


@router.get("", dependencies=[Depends(require_permissions("settings:view"))])
def list_masters(db: Session = Depends(get_db)):
    departments = db.scalars(select(Department).where(Department.deleted_at.is_(None)).order_by(Department.name)).all()
    designations = db.scalars(select(Designation).where(Designation.deleted_at.is_(None)).order_by(Designation.title)).all()
    leave_types = db.scalars(select(LeaveType).where(LeaveType.deleted_at.is_(None)).order_by(LeaveType.name)).all()
    lookups = db.scalars(select(LookupValue).where(LookupValue.deleted_at.is_(None)).order_by(LookupValue.category, LookupValue.sort_order, LookupValue.label)).all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for lookup in lookups:
        grouped.setdefault(lookup.category, []).append(_record_payload(lookup, "lookups"))
    return {
        "departments": [_record_payload(item, "departments") for item in departments],
        "designations": [_record_payload(item, "designations") for item in designations],
        "leave_types": [_record_payload(item, "leave_types") for item in leave_types],
        "lookups": grouped,
    }


@router.post("/{master_type}", dependencies=[Depends(require_permissions("settings:view"))])
def create_master(master_type: str, payload: MasterPayload, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if master_type == "departments":
        parent_id = _validate_parent_department(db, payload.parent_department_id, None)
        record = Department(
            name=_required(payload.name, "name"),
            code=normalize_code(payload.code) if payload.code else None,
            description=payload.description,
            parent_department_id=parent_id,
            active=payload.active,
        )
    elif master_type == "designations":
        record = Designation(
            title=_required(payload.name or payload.title, "name"),
            code=normalize_code(payload.code) if payload.code else None,
            level=payload.level,
            description=payload.description,
        )
    elif master_type == "leave_types":
        record = LeaveType(name=_required(payload.name, "name"), code=normalize_code(_required(payload.code, "code")), category=payload.category or "PAID", annual_allocation=payload.annual_allocation, annual_quota=payload.annual_allocation, is_paid=(payload.category or "PAID") != "UNPAID", carry_forward_allowed=payload.carry_forward_allowed, requires_approval=payload.requires_approval, affects_payroll=payload.affects_payroll, active=payload.active, description=payload.description)
    elif master_type == "lookups":
        record = LookupValue(category=_required(payload.category, "category"), code=normalize_code(_required(payload.code, "code")), label=_required(payload.label or payload.name, "label"), sort_order=payload.sort_order, active=payload.active)
    else:
        raise HTTPException(status_code=404, detail="Unsupported master type")
    return _save_master(db, current_user, record, master_type, "created")


@router.patch("/{master_type}/{record_id}", dependencies=[Depends(require_permissions("settings:view"))])
def update_master(master_type: str, record_id: UUID, payload: MasterPayload, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    model = _model_for(master_type)
    record = db.get(model, record_id)
    if not record or record.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Master record not found")
    old_value = _record_payload(record, master_type)
    values = payload.model_dump(exclude_unset=True)
    if master_type == "departments":
        if "parent_department_id" in values:
            values["parent_department_id"] = _validate_parent_department(db, values["parent_department_id"], record.id)
        if "code" in values and values["code"] is not None:
            values["code"] = normalize_code(values["code"])
        _assign(record, values, {"name", "code", "description", "parent_department_id", "active"})
    elif master_type == "designations":
        if "name" in values:
            record.title = values.pop("name")
        if "code" in values and values["code"] is not None:
            values["code"] = normalize_code(values["code"])
        _assign(record, values, {"title", "code", "description", "level"})
    elif master_type == "leave_types":
        if "code" in values and values["code"] is not None:
            values["code"] = normalize_code(values["code"])
        _assign(record, values, {"name", "code", "category", "annual_allocation", "carry_forward_allowed", "requires_approval", "affects_payroll", "active", "description"})
        record.annual_quota = record.annual_allocation
        record.is_paid = str(record.category) != "UNPAID"
    else:
        if "name" in values and "label" not in values:
            values["label"] = values.pop("name")
        if "code" in values and values["code"] is not None:
            values["code"] = normalize_code(values["code"])
        _assign(record, values, {"category", "code", "label", "sort_order", "active"})
    return _save_master(db, current_user, record, master_type, "updated", old_value)


@router.delete("/{master_type}/{record_id}", dependencies=[Depends(require_permissions("settings:view"))])
def delete_master(master_type: str, record_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    model = _model_for(master_type)
    record = db.get(model, record_id)
    if not record or record.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Master record not found")

    # NEW: block deletion when the master record is still in use, the same
    # way payroll.py blocks deleting a salary component/structure that a
    # structure/assignment still references.
    if master_type == "departments":
        employees = find_department_employees(db, record.id)
        if employees:
            names = ", ".join(_employee_display_name(e) for e in employees[:3])
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete this department because {len(employees)} employee(s) are assigned to it, including: {names}. Reassign them first.",
            )
    elif master_type == "designations":
        employees = find_designation_employees(db, record.id)
        if employees:
            names = ", ".join(_employee_display_name(e) for e in employees[:3])
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete this designation because {len(employees)} employee(s) hold it, including: {names}. Reassign them first.",
            )
    elif master_type == "leave_types":
        usage_count = find_leave_type_usage_count(db, record.id)
        if usage_count:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete this leave type because {usage_count} leave request(s)/balance record(s) still reference it.",
            )

    old_value = _record_payload(record, master_type)
    record.deleted_at = datetime.now(timezone.utc)
    if hasattr(record, "active"):
        record.active = False
    return _save_master(db, current_user, record, master_type, "deleted", old_value)


def _save_master(db: Session, user: User, record: Any, master_type: str, action: str, old_value: dict | None = None):
    try:
        db.add(record)
        db.flush()
        new_value = _record_payload(record, master_type)
        db.add(AuditLog(entity_type=f"master.{master_type}", entity_id=record.id, action=f"master.{master_type}.{action}", old_value=old_value, new_value=new_value, performed_by=user.id))
        db.commit()
        return new_value
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="A master record with this name or code already exists.") from exc


def _model_for(master_type: str):
    models = {"departments": Department, "designations": Designation, "leave_types": LeaveType, "lookups": LookupValue}
    if master_type not in models:
        raise HTTPException(status_code=404, detail="Unsupported master type")
    return models[master_type]


def _required(value: str | None, field: str) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=400, detail=f"{field} is required")
    return value.strip()


def _assign(record: Any, values: dict[str, Any], allowed: set[str]) -> None:
    for key, value in values.items():
        if key in allowed:
            setattr(record, key, value)
