from __future__ import annotations
from typing import Any
from uuid import UUID
from app.agents.approval_agent.handlers import handler_registry
from app.agents.payroll_agent.services import PayrollService
from app.db.session import SessionLocal
def process_payroll(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = payload.get("run_id")
    if not run_id:
        raise LookupError("Payroll approval payload does not include run_id")
    approved_by = payload.get("approved_by")
    db = SessionLocal()
    try:
        result = PayrollService(db).approve_payroll_run(UUID(str(run_id)), UUID(str(approved_by)) if approved_by else None)
        db.commit()
        return {
            "status": "executed",
            "message": "Payroll run approved.",
            "payroll_run": result,
            "structured_response": {
                "type": "payroll_run_approved",
                "title": "Payroll run approved",
                "summary": result,
            },
        }
    finally:
        db.close()
# NEW: previously no handler was registered for "payroll.process.reject", so
# rejecting a payroll run's approval request fell through to the shared
# placeholder_handler — it returned a "placeholder_executed" response but
# never touched PayrollRun.status, silently leaving the run stuck at
# PENDING_APPROVAL. This mirrors process_payroll's pattern (open a session,
# call the matching PayrollService method, commit, return a structured
# response) but calls the new reject_payroll_run instead.
def reject_payroll(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = payload.get("run_id")
    if not run_id:
        raise LookupError("Payroll rejection payload does not include run_id")
    rejected_by = payload.get("rejected_by") or payload.get("approved_by")
    reason = payload.get("reason") or payload.get("rejection_reason")
    db = SessionLocal()
    try:
        result = PayrollService(db).reject_payroll_run(
            UUID(str(run_id)),
            UUID(str(rejected_by)) if rejected_by else None,
            reason,
        )
        db.commit()
        return {
            "status": "executed",
            "message": "Payroll run rejected.",
            "payroll_run": result,
            "structured_response": {
                "type": "payroll_run_rejected",
                "title": "Payroll run rejected",
                "summary": result,
            },
        }
    finally:
        db.close()
def generate_bank_sheet(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = payload.get("run_id")
    if not run_id:
        raise LookupError("Bank sheet approval payload does not include run_id")
    approved_by = payload.get("approved_by")
    db = SessionLocal()
    try:
        result = PayrollService(db).generate_bank_sheet(UUID(str(run_id)), UUID(str(approved_by)) if approved_by else None)
        db.commit()
        return {
            "status": "executed",
            "message": "Bank sheet generated.",
            "bank_sheet_summary": result,
            "structured_response": {
                "type": "bank_sheet_generated",
                "title": "Bank sheet generated",
                "summary": result,
            },
        }
    finally:
        db.close()
handler_registry.register("payroll.process", process_payroll)
handler_registry.register("payroll.process.reject", reject_payroll)
handler_registry.register("payroll.generate_bank_sheet", generate_bank_sheet)