from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.payroll import SalaryComponent
from app.models.payroll import SalaryStructure, SalaryStructureItem
from app.models.payroll import EmployeeSalaryAssignment, SalaryAssignmentStatus
from app.agents.payroll_agent.tools import normalize_code
from datetime import datetime, timezone

router = APIRouter()


class SalaryComponentResponse(BaseModel):
    id: UUID
    name: str
    code: str
    type: str
    calculation_type: str
    calculation_value: float | None = None
    formula: str | None = None
    reference_component_code: str | None = None
    taxable: bool
    active: bool
    created_at: str | None = None
    updated_at: str | None = None


class SalaryComponentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    code: str | None = None
    type: str = Field(..., pattern="^(earning|deduction)$")
    calculation_type: str = Field(..., pattern="^(fixed|percentage|formula|balance)$")
    calculation_value: float | None = None
    formula: str | None = None
    reference_component_code: str | None = None
    taxable: bool = True
    active: bool = True


class SalaryComponentUpdateRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    type: str | None = Field(None, pattern="^(earning|deduction)$")
    calculation_type: str | None = Field(None, pattern="^(fixed|percentage|formula|balance)$")
    calculation_value: float | None = None
    formula: str | None = None
    reference_component_code: str | None = None
    taxable: bool | None = None
    active: bool | None = None


def build_response(component: SalaryComponent) -> SalaryComponentResponse:
    return SalaryComponentResponse(
        id=component.id,
        name=component.name,
        code=component.code,
        type=component.type,
        calculation_type=component.calculation_type,
        calculation_value=float(component.calculation_value) if component.calculation_value is not None else None,
        formula=component.formula,
        reference_component_code=component.reference_component_code,
        taxable=component.taxable,
        active=component.active,
        created_at=component.created_at.isoformat() if component.created_at else None,
        updated_at=component.updated_at.isoformat() if component.updated_at else None,
    )


def find_active_structure_assignments(db: Session, structure_id: UUID) -> list[EmployeeSalaryAssignment]:
    """Returns non-deleted EmployeeSalaryAssignment rows currently ACTIVE
    against this salary structure. Used to block structure deletion the
    same way find_component_structures blocks component deletion."""
    return list(
        db.scalars(
            select(EmployeeSalaryAssignment).where(
                EmployeeSalaryAssignment.deleted_at.is_(None),
                EmployeeSalaryAssignment.salary_structure_id == structure_id,
                EmployeeSalaryAssignment.status == SalaryAssignmentStatus.ACTIVE,
            )
        ).all()
    )


def find_component_structures(db: Session, component_code: str) -> list[SalaryStructure]:
    return list(
        db.scalars(
            select(SalaryStructure)
            .join(SalaryStructureItem, SalaryStructureItem.structure_id == SalaryStructure.id)
            .where(
                SalaryStructure.deleted_at.is_(None),
                SalaryStructureItem.deleted_at.is_(None),
                SalaryStructureItem.component_code == component_code,
            )
            .order_by(SalaryStructure.name)
        ).all()
    )


class StructureItemRequest(BaseModel):
    component_code: str
    calculation_type: str = Field(..., pattern="^(fixed|percentage|formula|balance)$")
    calculation_value: float | None = None
    formula: str | None = None
    reference_component_code: str | None = None


class SalaryStructureResponse(BaseModel):
    id: UUID
    name: str
    code: str
    description: str | None = None
    active: bool
    item_count: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SalaryStructureItemResponse(BaseModel):
    id: UUID
    component_code: str
    calculation_type: str
    calculation_value: float | None = None
    formula: str | None = None
    reference_component_code: str | None = None
    sort_order: int | None = None


class SalaryStructureDetailResponse(SalaryStructureResponse):
    items: list[SalaryStructureItemResponse] = []


class SalaryStructureCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    code: str | None = None
    description: str | None = None
    items: list[StructureItemRequest]


class SalaryStructureUpdateRequest(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    active: bool | None = None
    items: list[StructureItemRequest] | None = None


def _structure_snapshot(structure: SalaryStructure) -> dict[str, Any]:
    return {
        "id": str(structure.id),
        "name": structure.name,
        "code": structure.code,
        "description": structure.description,
        "active": structure.active,
        "items": [
            {
                "component_code": item.component_code,
                "calculation_type": item.calculation_type,
                "calculation_value": float(item.calculation_value) if item.calculation_value is not None else None,
                "formula": item.formula,
                "reference_component_code": item.reference_component_code,
                "sort_order": item.sort_order,
            }
            for item in (structure.items or [])
            # Filter out soft-deleted items, otherwise a post-update
            # snapshot (taken after the old items are marked deleted_at but
            # before the session forgets about them) double-counts old +
            # new items in the audit trail.
            if getattr(item, "deleted_at", None) is None
        ],
    }


def _validate_calculation_fields(
    calculation_type: str,
    calculation_value: float | None,
    formula: str | None,
    reference_component_code: str | None,
) -> None:
    if calculation_type == "fixed":
        if calculation_value is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="calculation_value is required when calculation_type is 'fixed'.",
            )
    elif calculation_type == "percentage":
        if calculation_value is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="calculation_value is required when calculation_type is 'percentage'.",
            )
        if not reference_component_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reference_component_code is required when calculation_type is 'percentage' (percentage of what?).",
            )
    elif calculation_type == "formula":
        if not formula or not formula.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="formula is required when calculation_type is 'formula'.",
            )
    # "balance" (assumed to mean "whatever remains") has no required
    # companion field.


def _validate_component_codes_exist(db: Session, codes: set[str]) -> set[str]:
    """Returns the subset of `codes` that do NOT correspond to an active,
    non-deleted salary component."""
    if not codes:
        return set()
    existing = set(
        db.scalars(
            select(SalaryComponent.code).where(
                SalaryComponent.code.in_(codes),
                SalaryComponent.deleted_at.is_(None),
                SalaryComponent.active.is_(True),
            )
        ).all()
    )
    return codes - existing


def _validate_structure_component_codes(db: Session, items: list[StructureItemRequest]) -> None:
    if not items:
        return

    normalized_codes = [normalize_code(item.component_code) for item in items]

    seen: set[str] = set()
    duplicates: set[str] = set()
    for code in normalized_codes:
        if code in seen:
            duplicates.add(code)
        seen.add(code)
    if duplicates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Duplicate component code(s) in structure: {', '.join(sorted(duplicates))}.",
        )

    for item in items:
        _validate_calculation_fields(
            calculation_type=item.calculation_type,
            calculation_value=item.calculation_value,
            formula=item.formula,
            reference_component_code=item.reference_component_code,
        )

    requested_codes = set(normalized_codes)
    reference_codes = {
        normalize_code(item.reference_component_code) for item in items if item.reference_component_code
    }
    missing = _validate_component_codes_exist(db, requested_codes | reference_codes)
    if missing:
        names = ", ".join(sorted(missing))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown or inactive salary component code(s): {names}.",
        )


@router.get("/structures", response_model=list[SalaryStructureResponse], dependencies=[Depends(require_permissions("payroll:view"))])
def list_salary_structures(db: Session = Depends(get_db)) -> list[SalaryStructureResponse]:
    rows = db.scalars(select(SalaryStructure).where(SalaryStructure.deleted_at.is_(None)).order_by(SalaryStructure.name.asc())).all()
    def build(s: SalaryStructure) -> SalaryStructureResponse:
        return SalaryStructureResponse(
            id=s.id,
            name=s.name,
            code=s.code,
            description=s.description,
            active=s.active,
            item_count=len(s.items) if s.items is not None else 0,
            created_at=s.created_at.isoformat() if s.created_at else None,
            updated_at=s.updated_at.isoformat() if s.updated_at else None,
        )

    return [build(s) for s in rows]


def _build_structure_detail(structure: SalaryStructure) -> SalaryStructureDetailResponse:
    items = [item for item in (structure.items or []) if getattr(item, "deleted_at", None) is None]
    items.sort(key=lambda i: i.sort_order or 0)
    return SalaryStructureDetailResponse(
        id=structure.id,
        name=structure.name,
        code=structure.code,
        description=structure.description,
        active=structure.active,
        item_count=len(items),
        created_at=structure.created_at.isoformat() if structure.created_at else None,
        updated_at=structure.updated_at.isoformat() if structure.updated_at else None,
        items=[
            SalaryStructureItemResponse(
                id=item.id,
                component_code=item.component_code,
                calculation_type=item.calculation_type,
                calculation_value=float(item.calculation_value) if item.calculation_value is not None else None,
                formula=item.formula,
                reference_component_code=item.reference_component_code,
                sort_order=item.sort_order,
            )
            for item in items
        ],
    )


@router.get("/structures/{structure_id}", response_model=SalaryStructureDetailResponse, dependencies=[Depends(require_permissions("payroll:view"))])
def get_salary_structure(structure_id: UUID, db: Session = Depends(get_db)) -> SalaryStructureDetailResponse:
    structure = db.get(SalaryStructure, structure_id)
    if not structure or structure.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salary structure not found")
    return _build_structure_detail(structure)


# FIX: this and every other write endpoint in this file (PUT/DELETE
# structures, POST/PUT/DELETE components) were gated by payroll:view —
# a read permission guarding real writes. Now payroll:execute, matching
# the same fix already applied to coordinator_agent/router.py's
# /command endpoint and salary_assignment_agent/api.py's /command
# endpoint earlier today. GET endpoints in this file remain on
# payroll:view, unchanged.
@router.post("/structures", response_model=SalaryStructureDetailResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permissions("payroll:execute"))])
def create_salary_structure(
    payload: SalaryStructureCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalaryStructureDetailResponse:
    _validate_structure_component_codes(db, payload.items)
    now = datetime.now(timezone.utc)
    code = normalize_code(payload.code) if payload.code else f"SS_{normalize_code(payload.name)}"
    try:
        structure = SalaryStructure(
            name=payload.name,
            code=code,
            description=payload.description,
            active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(structure)
        db.flush()
        order = 0
        for it in payload.items:
            order += 1
            item = SalaryStructureItem(
                structure_id=structure.id,
                component_code=normalize_code(it.component_code),
                calculation_type=it.calculation_type,
                calculation_value=it.calculation_value,
                formula=it.formula,
                reference_component_code=normalize_code(it.reference_component_code) if it.reference_component_code else None,
                sort_order=order,
                created_at=now,
                updated_at=now,
            )
            db.add(item)
        db.flush()
        db.add(
            AuditLog(
                entity_type="salary_structure",
                entity_id=structure.id,
                action="payroll.structure.created",
                new_value=_structure_snapshot(structure),
                performed_by=current_user.id,
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Salary structure code or name already exists.") from exc
    db.refresh(structure)
    return _build_structure_detail(structure)


@router.put("/structures/{structure_id}", response_model=SalaryStructureDetailResponse, dependencies=[Depends(require_permissions("payroll:execute"))])
def update_salary_structure(
    structure_id: UUID,
    payload: SalaryStructureUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalaryStructureDetailResponse:
    structure = db.get(SalaryStructure, structure_id)
    if not structure or structure.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salary structure not found")

    old_value = _structure_snapshot(structure)
    now = datetime.now(timezone.utc)
    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data and update_data["name"] is not None:
        update_data["name"] = update_data["name"].strip()
    if "code" in update_data and update_data["code"] is not None:
        update_data["code"] = normalize_code(update_data["code"])

    items_payload = update_data.pop("items", None)
    parsed_items: list[StructureItemRequest] | None = None
    if items_payload is not None:
        parsed_items = [StructureItemRequest(**it) for it in items_payload]
        _validate_structure_component_codes(db, parsed_items)

    try:
        for field, value in update_data.items():
            setattr(structure, field, value)
        structure.updated_at = now

        if parsed_items is not None:
            for item in list(structure.items or []):
                if getattr(item, "deleted_at", None) is None:
                    item.deleted_at = now
                    item.updated_at = now
                    db.add(item)
            order = 0
            for it in parsed_items:
                order += 1
                db.add(
                    SalaryStructureItem(
                        structure_id=structure.id,
                        component_code=normalize_code(it.component_code),
                        calculation_type=it.calculation_type,
                        calculation_value=it.calculation_value,
                        formula=it.formula,
                        reference_component_code=normalize_code(it.reference_component_code) if it.reference_component_code else None,
                        sort_order=order,
                        created_at=now,
                        updated_at=now,
                    )
                )
            db.flush()

        db.add(
            AuditLog(
                entity_type="salary_structure",
                entity_id=structure.id,
                action="payroll.structure.updated",
                old_value=old_value,
                new_value=_structure_snapshot(structure),
                performed_by=current_user.id,
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Salary structure could not be updated. Check its name, code, and items.") from exc
    db.refresh(structure)
    return _build_structure_detail(structure)


@router.delete("/structures/{structure_id}", dependencies=[Depends(require_permissions("payroll:execute"))])
def delete_salary_structure(
    structure_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    structure = db.get(SalaryStructure, structure_id)
    if not structure or structure.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salary structure not found")

    active_assignments = find_active_structure_assignments(db, structure.id)
    if active_assignments:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete this structure because {len(active_assignments)} employee(s) "
                "currently have an active salary assignment against it. Reassign or end those "
                "assignments first."
            ),
        )

    old_value = _structure_snapshot(structure)
    now = datetime.now(timezone.utc)
    structure.active = False
    structure.deleted_at = now
    structure.updated_at = now
    db.add(structure)
    db.add(
        AuditLog(
            entity_type="salary_structure",
            entity_id=structure.id,
            action="payroll.structure.deleted",
            old_value=old_value,
            new_value=_structure_snapshot(structure),
            performed_by=current_user.id,
        )
    )
    db.commit()
    return {"status": "deleted", "structure_id": str(structure.id)}


@router.get("/components", response_model=list[SalaryComponentResponse], dependencies=[Depends(require_permissions("payroll:view"))])
def list_salary_components(db: Session = Depends(get_db)) -> list[SalaryComponentResponse]:
    components = db.scalars(select(SalaryComponent).where(SalaryComponent.deleted_at.is_(None)).order_by(SalaryComponent.name.asc())).all()
    return [build_response(component) for component in components]


@router.post("/components", response_model=SalaryComponentResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permissions("payroll:execute"))])
def create_salary_component(
    payload: SalaryComponentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalaryComponentResponse:
    now = datetime.now(timezone.utc)
    name = payload.name.strip()
    code = normalize_code(payload.code or name)

    _validate_calculation_fields(
        calculation_type=payload.calculation_type,
        calculation_value=payload.calculation_value,
        formula=payload.formula,
        reference_component_code=payload.reference_component_code,
    )

    reference_code = normalize_code(payload.reference_component_code) if payload.reference_component_code else None
    if reference_code:
        if reference_code == code:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A component cannot reference itself.")
        missing = _validate_component_codes_exist(db, {reference_code})
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown or inactive reference component code: {reference_code}.",
            )

    existing = db.scalar(
        select(SalaryComponent).where(
            or_(
                SalaryComponent.code == code,
                func.lower(SalaryComponent.name) == name.lower(),
            )
        )
    )

    if existing and existing.deleted_at is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A salary component with this name or code already exists.")

    old_value = build_response(existing).model_dump(mode="json") if existing else None

    component = existing or SalaryComponent(created_at=now)
    component.name = name
    component.code = code
    component.type = payload.type
    component.calculation_type = payload.calculation_type
    component.calculation_value = payload.calculation_value
    component.formula = payload.formula
    component.reference_component_code = reference_code
    component.taxable = payload.taxable
    component.active = payload.active
    component.deleted_at = None
    component.updated_at = now
    db.add(component)
    db.flush()
    db.add(
        AuditLog(
            entity_type="salary_component",
            entity_id=component.id,
            action="payroll.component.created",
            old_value=old_value,
            new_value=build_response(component).model_dump(mode="json"),
            performed_by=current_user.id,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Salary component could not be saved. Check its name, code, and values.") from exc
    db.refresh(component)
    return build_response(component)


@router.put("/components/{component_id}", response_model=SalaryComponentResponse, dependencies=[Depends(require_permissions("payroll:execute"))])
def update_salary_component(
    component_id: UUID,
    payload: SalaryComponentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalaryComponentResponse:
    component = db.get(SalaryComponent, component_id)
    if not component or component.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salary component not found")
    old_value = build_response(component).model_dump(mode="json")
    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data and update_data["name"] is not None:
        update_data["name"] = update_data["name"].strip()
    if "code" in update_data and update_data["code"] is not None:
        update_data["code"] = normalize_code(update_data["code"])
    if "reference_component_code" in update_data and update_data["reference_component_code"]:
        update_data["reference_component_code"] = normalize_code(update_data["reference_component_code"])

    effective_calculation_type = update_data.get("calculation_type", component.calculation_type)
    effective_calculation_value = update_data.get("calculation_value", component.calculation_value)
    effective_formula = update_data.get("formula", component.formula)
    effective_reference = update_data.get("reference_component_code", component.reference_component_code)
    effective_code = update_data.get("code", component.code)

    _validate_calculation_fields(
        calculation_type=effective_calculation_type,
        calculation_value=effective_calculation_value,
        formula=effective_formula,
        reference_component_code=effective_reference,
    )

    if effective_reference:
        if effective_reference == effective_code:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A component cannot reference itself.")
        missing = _validate_component_codes_exist(db, {effective_reference})
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown or inactive reference component code: {effective_reference}.",
            )

    calculation_affecting_fields = {"type", "calculation_type", "calculation_value", "formula", "reference_component_code", "active"}
    if calculation_affecting_fields.intersection(update_data.keys()):
        structures = find_component_structures(db, component.code)
        if structures:
            names = ", ".join(structure.name for structure in structures[:3])
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot change calculation details because this component is used by salary structure(s): {names}.",
            )

    if "code" in update_data and update_data["code"] is not None and update_data["code"] != component.code:
        structures = find_component_structures(db, component.code)
        if structures:
            names = ", ".join(structure.name for structure in structures[:3])
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Code cannot be changed because this component is used by salary structure(s): {names}.",
            )

    for field, value in update_data.items():
        setattr(component, field, value)
    component.updated_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            entity_type="salary_component",
            entity_id=component.id,
            action="payroll.component.updated",
            old_value=old_value,
            new_value=build_response(component).model_dump(mode="json"),
            performed_by=current_user.id,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Salary component could not be updated. Check its name, code, and values.") from exc
    db.refresh(component)
    return build_response(component)


@router.delete("/components/{component_id}", dependencies=[Depends(require_permissions("payroll:execute"))])
def delete_salary_component(
    component_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    component = db.get(SalaryComponent, component_id)
    if not component or component.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salary component not found")
    structures = find_component_structures(db, component.code)
    if structures:
        names = ", ".join(structure.name for structure in structures[:3])
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete this component because it is used by salary structure(s): {names}. Remove it from those structures first.",
        )
    old_value = build_response(component).model_dump(mode="json")
    component.active = False
    component.deleted_at = datetime.now(timezone.utc)
    component.updated_at = datetime.now(timezone.utc)
    db.add(component)
    db.add(
        AuditLog(
            entity_type="salary_component",
            entity_id=component.id,
            action="payroll.component.deleted",
            old_value=old_value,
            new_value=build_response(component).model_dump(mode="json"),
            performed_by=current_user.id,
        )
    )
    db.commit()
    return {"status": "deleted", "component_id": str(component.id)}