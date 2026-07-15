from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.employee import Employee, EmployeeDocument
from app.models.employee.models import DocumentStatus

router = APIRouter()


class DocumentCreateRequest(BaseModel):
    employee_id: UUID
    document_type: str
    document_url: str
    status: str = "PENDING"

    # NEW: document_type/document_url previously only had .strip() applied
    # at the endpoint after validation, so an all-whitespace or empty string
    # would pass Pydantic and then get stored as "" once stripped. Validating
    # here rejects that before it ever reaches the DB.
    @field_validator("document_type", "document_url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("This field cannot be blank.")
        return value.strip()

    # NEW: status was accepted as any free-text string with no check against
    # the actual DocumentStatus enum (PENDING/VERIFIED/REJECTED), so a typo
    # or arbitrary value could be stored and would then silently fail to
    # match anything the frontend's StatusBadge/filtering logic expects.
    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        normalized = value.strip().upper()
        valid = {status.value for status in DocumentStatus}
        if normalized not in valid:
            raise ValueError(f"status must be one of: {', '.join(sorted(valid))}")
        return normalized


# NEW: there was previously no way to correct a document record once
# created — document_type/document_url were write-once at creation. This
# request type backs a PATCH endpoint for plain field edits only.
#
# Verification (status -> VERIFIED/REJECTED) is intentionally NOT handled
# here anymore. It was originally folded into this same PATCH gated on
# documents:view, but the frontend (DocumentsPage.tsx / documents.ts) calls
# dedicated POST /{id}/verify and /{id}/reject routes gated on the separate
# documents:verify permission that already exists in auth_service.py. Status
# was removed from this schema so a documents:view-only user can no longer
# reach verification by slipping a status field into a plain edit request.
class DocumentUpdateRequest(BaseModel):
    document_type: str | None = None
    document_url: str | None = None

    @field_validator("document_type", "document_url")
    @classmethod
    def _not_blank_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("This field cannot be blank.")
        return value.strip()


# NEW: backs POST /{document_id}/reject. reason is optional, matching the
# frontend's textarea which can be left blank.
class DocumentRejectRequest(BaseModel):
    reason: str | None = None


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


@router.patch("/{document_id}", dependencies=[Depends(require_permissions("documents:view"))])
def update_document(
    document_id: UUID,
    payload: DocumentUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = db.scalar(
        select(EmployeeDocument)
        .options(selectinload(EmployeeDocument.employee))
        .where(EmployeeDocument.id == document_id, EmployeeDocument.deleted_at.is_(None))
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    old_value = document_payload(document)
    values = payload.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc)

    if "document_type" in values and values["document_type"] is not None:
        document.document_type = values["document_type"]
    if "document_url" in values and values["document_url"] is not None:
        document.document_url = values["document_url"]

    document.updated_at = now
    db.add(document)
    db.add(
        AuditLog(
            entity_type="employee_document",
            entity_id=document.id,
            action="document.updated_from_form",
            old_value=old_value,
            new_value=document_payload(document),
            performed_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(document)
    return document_payload(document)


def _get_document_or_404(db: Session, document_id: UUID) -> EmployeeDocument:
    document = db.scalar(
        select(EmployeeDocument)
        .options(selectinload(EmployeeDocument.employee))
        .where(EmployeeDocument.id == document_id, EmployeeDocument.deleted_at.is_(None))
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


# NEW: matches documents.ts's verifyDocument(documentId) -> POST /{id}/verify.
# Gated on documents:verify (a real, separately-seeded permission — distinct
# from documents:view, which only gates create/edit/delete/list). This is
# what makes "VERIFIED" an actual second-person check instead of self-reported
# status at creation time: verified_by/verified_at are set here, not by the
# person who created the record.
@router.post("/{document_id}/verify", dependencies=[Depends(require_permissions("documents:verify"))])
def verify_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = _get_document_or_404(db, document_id)
    old_value = document_payload(document)
    now = datetime.now(timezone.utc)

    document.status = DocumentStatus.VERIFIED.value
    document.verified_by = current_user.id
    document.verified_at = now
    document.updated_at = now
    db.add(document)
    db.add(
        AuditLog(
            entity_type="employee_document",
            entity_id=document.id,
            action="document.verified",
            old_value=old_value,
            new_value=document_payload(document),
            performed_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(document)
    return document_payload(document)


# NEW: matches documents.ts's rejectDocument(documentId, reason) -> POST
# /{id}/reject with an optional reason in the body. Same documents:verify
# gate as verify above — rejecting is the same trust action as approving,
# just the other outcome, so it should require the same permission.
@router.post("/{document_id}/reject", dependencies=[Depends(require_permissions("documents:verify"))])
def reject_document(
    document_id: UUID,
    payload: DocumentRejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = _get_document_or_404(db, document_id)
    old_value = document_payload(document)
    now = datetime.now(timezone.utc)

    document.status = DocumentStatus.REJECTED.value
    document.verified_by = current_user.id
    document.verified_at = now
    document.updated_at = now
    db.add(document)
    db.add(
        AuditLog(
            entity_type="employee_document",
            entity_id=document.id,
            action="document.rejected",
            old_value=old_value,
            new_value={**document_payload(document), "reason": payload.reason},
            performed_by=current_user.id,
        )
    )
    db.commit()
    db.refresh(document)
    return document_payload(document)


@router.delete("/{document_id}", dependencies=[Depends(require_permissions("documents:view"))])
def delete_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = _get_document_or_404(db, document_id)
    old_value = document_payload(document)
    document.deleted_at = datetime.now(timezone.utc)
    document.updated_at = datetime.now(timezone.utc)
    db.add(document)
    db.add(
        AuditLog(
            entity_type="employee_document",
            entity_id=document.id,
            action="document.deleted_from_form",
            old_value=old_value,
            new_value=None,
            performed_by=current_user.id,
        )
    )
    db.commit()
    return {"status": "deleted", "document_id": str(document.id)}