from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.employee import Employee, EmployeeDocument

router = APIRouter()


class DocumentCreateRequest(BaseModel):
    employee_id: UUID
    document_type: str
    document_url: str
    status: str = "PENDING"


def document_payload(document: EmployeeDocument) -> dict:
    employee = document.employee
    name = " ".join(part for part in (employee.first_name, employee.last_name) if part).strip() if employee else ""
    return {
        "id": str(document.id),
        "employee_id": str(document.employee_id),
        "employee_name": name or employee.employee_code if employee else "Unknown employee",
        "document_type": document.document_type,
        "document_url": document.document_url,
        "status": str(document.status),
        "verified_at": document.verified_at.isoformat() if document.verified_at else None,
        "created_at": document.created_at.isoformat() if document.created_at else None,
    }


@router.get("", dependencies=[Depends(require_permissions("documents:view"))])
def list_documents(db: Session = Depends(get_db)):
    documents = db.scalars(
        select(EmployeeDocument)
        .options(selectinload(EmployeeDocument.employee))
        .where(EmployeeDocument.deleted_at.is_(None))
        .order_by(EmployeeDocument.created_at.desc())
    ).all()
    return [document_payload(document) for document in documents]


@router.post("", dependencies=[Depends(require_permissions("documents:view"))])
def create_document(
    payload: DocumentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    employee = db.get(Employee, payload.employee_id)
    if not employee or employee.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Employee not found")
    document = EmployeeDocument(
        employee_id=employee.id,
        document_type=payload.document_type.strip(),
        document_url=payload.document_url.strip(),
        status=payload.status,
    )
    db.add(document)
    db.flush()
    db.add(
        AuditLog(
            entity_type="employee_document",
            entity_id=document.id,
            action="document.created_from_form",
            new_value={"employee_id": str(employee.id), "document_type": document.document_type, "status": str(document.status)},
            performed_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(document)
    document.employee = employee
    return document_payload(document)
