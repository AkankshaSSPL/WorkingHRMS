from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_permissions
from app.db.session import get_db
from app.models.payroll import SalaryComponent
from app.models.payroll import SalaryStructure, SalaryStructureItem
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


class SalaryStructureCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    code: str | None = None
    description: str | None = None
    items: list[StructureItemRequest]


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


@router.post("/structures", response_model=SalaryStructureResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permissions("payroll:view"))])
def create_salary_structure(payload: SalaryStructureCreateRequest, db: Session = Depends(get_db)) -> SalaryStructureResponse:
    now = datetime.now(timezone.utc)
    # Ensure a clean SS_ prefixed code when not provided
    code = payload.code or f"SS_{normalize_code(payload.name)}"
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
            component_code=it.component_code,
            calculation_type=it.calculation_type,
            calculation_value=it.calculation_value,
            formula=it.formula,
            reference_component_code=it.reference_component_code,
            sort_order=order,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Salary structure code or name already exists.") from exc
    db.refresh(structure)
    return SalaryStructureResponse(
        id=structure.id,
        name=structure.name,
        code=structure.code,
        description=structure.description,
        active=structure.active,
        item_count=len(structure.items) if structure.items is not None else 0,
        created_at=structure.created_at.isoformat() if structure.created_at else None,
        updated_at=structure.updated_at.isoformat() if structure.updated_at else None,
    )


@router.get("/components", response_model=list[SalaryComponentResponse], dependencies=[Depends(require_permissions("payroll:view"))])
def list_salary_components(db: Session = Depends(get_db)) -> list[SalaryComponentResponse]:
    components = db.scalars(select(SalaryComponent).where(SalaryComponent.deleted_at.is_(None)).order_by(SalaryComponent.name.asc())).all()
    return [build_response(component) for component in components]


@router.post("/components", response_model=SalaryComponentResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permissions("payroll:view"))])
def create_salary_component(payload: SalaryComponentCreateRequest, db: Session = Depends(get_db)) -> SalaryComponentResponse:
    now = datetime.now(timezone.utc)
    name = payload.name.strip()
    code = normalize_code(payload.code or name)
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

    component = existing or SalaryComponent(created_at=now)
    component.name = name
    component.code = code
    component.type = payload.type
    component.calculation_type = payload.calculation_type
    component.calculation_value = payload.calculation_value
    component.formula = payload.formula
    component.reference_component_code = normalize_code(payload.reference_component_code) if payload.reference_component_code else None
    component.taxable = payload.taxable
    component.active = payload.active
    component.deleted_at = None
    component.updated_at = now
    db.add(component)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Salary component could not be saved. Check its name, code, and values.") from exc
    db.refresh(component)
    return build_response(component)


@router.put("/components/{component_id}", response_model=SalaryComponentResponse, dependencies=[Depends(require_permissions("payroll:view"))])
def update_salary_component(component_id: UUID, payload: SalaryComponentUpdateRequest, db: Session = Depends(get_db)) -> SalaryComponentResponse:
    component = db.get(SalaryComponent, component_id)
    if not component or component.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salary component not found")
    update_data = payload.model_dump(exclude_unset=True)
    if "name" in update_data and update_data["name"] is not None:
        update_data["name"] = update_data["name"].strip()
    if "code" in update_data and update_data["code"] is not None:
        next_code = normalize_code(update_data["code"])
        if next_code != component.code:
            structures = find_component_structures(db, component.code)
            if structures:
                names = ", ".join(structure.name for structure in structures[:3])
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Code cannot be changed because this component is used by salary structure(s): {names}.",
                )
        update_data["code"] = next_code
    if "reference_component_code" in update_data and update_data["reference_component_code"]:
        update_data["reference_component_code"] = normalize_code(update_data["reference_component_code"])
    for field, value in update_data.items():
        setattr(component, field, value)
    component.updated_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Salary component could not be updated. Check its name, code, and values.") from exc
    db.refresh(component)
    return build_response(component)


@router.delete("/components/{component_id}", dependencies=[Depends(require_permissions("payroll:view"))])
def delete_salary_component(component_id: UUID, db: Session = Depends(get_db)) -> dict[str, str]:
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
    component.active = False
    component.deleted_at = datetime.now(timezone.utc)
    component.updated_at = datetime.now(timezone.utc)
    db.add(component)
    db.commit()
    return {"status": "deleted", "component_id": str(component.id)}
