from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agents.employee_agent.tools import employee_display_name
from app.agents.salary_assignment_agent.services import SalaryAssignmentService
from app.models.audit import AuditLog
from app.models.payroll import PayrollRun, PayrollRunItem, PayrollRunStatus
from app.services.payroll_preparation_service import prepare_monthly_payroll_input


def money(value: Decimal | float | int | None) -> str:
    if value is None:
        return "Not available"
    return f"₹{float(value):,.0f}"


class PayrollService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.salary_service = SalaryAssignmentService(db)

    def get_run(self, month: int, year: int) -> PayrollRun | None:
        return self.db.scalar(
            select(PayrollRun)
            .where(PayrollRun.month == month, PayrollRun.year == year, PayrollRun.deleted_at.is_(None))
            .options(selectinload(PayrollRun.items))
        )

    def build_preview(self, month: int, year: int) -> dict[str, Any]:
        """Compute what a payroll run for this month/year would look like, without writing anything."""
        assignments = self.salary_service.active_assignments()
        if not assignments:
            return {
                "employee_count": 0,
                "rows": [],
                "total_gross": 0.0,
                "total_gross_display": money(0),
                "total_net": 0.0,
                "total_net_display": money(0),
                "missing_bank_details": [],
            }

        employee_ids = [assignment.employee_id for assignment in assignments]
        lop_rows = {
            row["employee_id"]: row
            for row in prepare_monthly_payroll_input(self.db, employee_ids=employee_ids, month=month, year=year)
        }

        rows: list[dict[str, Any]] = []
        total_gross = Decimal("0")
        total_net = Decimal("0")
        missing_bank_details: list[str] = []

        for assignment in assignments:
            employee = assignment.employee
            breakup = self.salary_service.calculate_breakup(assignment.salary_structure, assignment.gross_salary)
            lop = lop_rows.get(str(employee.id), {"working_days": 0, "lop_days": 0})

            gross = Decimal(str(assignment.gross_salary))
            deductions = Decimal(str(breakup["deductions"]))
            working_days = Decimal(str(lop.get("working_days") or 0))
            lop_days = Decimal(str(lop.get("lop_days") or 0))
            per_day_rate = (gross / working_days) if working_days > 0 else Decimal("0")
            lop_deduction = (per_day_rate * lop_days).quantize(Decimal("0.01"))

            net_salary = Decimal(str(breakup["net_salary"])) - lop_deduction
            if net_salary < 0:
                net_salary = Decimal("0")

            if not employee.bank_account_number or not employee.ifsc_code:
                missing_bank_details.append(employee_display_name(employee))

            total_gross += gross
            total_net += net_salary

            rows.append(
                {
                    "employee_id": str(employee.id),
                    "employee_name": employee_display_name(employee),
                    "gross_salary": float(gross),
                    "gross_salary_display": money(gross),
                    "deductions": float(deductions),
                    "deductions_display": money(deductions),
                    "lop_days": float(lop_days),
                    "lop_deduction": float(lop_deduction),
                    "lop_deduction_display": money(lop_deduction),
                    "net_salary": float(net_salary),
                    "net_salary_display": money(net_salary),
                    "bank_account_number": employee.bank_account_number,
                    "ifsc_code": employee.ifsc_code,
                }
            )

        return {
            "employee_count": len(rows),
            "rows": rows,
            "total_gross": float(total_gross),
            "total_gross_display": money(total_gross),
            "total_net": float(total_net),
            "total_net_display": money(total_net),
            "missing_bank_details": missing_bank_details,
        }

    def create_payroll_run(self, *, month: int, year: int, generated_by: UUID | None) -> PayrollRun:
        existing = self.get_run(month, year)
        # FIX: a payroll run previously REJECTED for this month/year was still
        # blocked from being reprocessed, because the guard below only allowed
        # DRAFT to be reused. A rejected run should be resubmittable, the same
        # way a fresh DRAFT is, so it's added to the allowed set here.
        if existing and existing.status not in (PayrollRunStatus.DRAFT, PayrollRunStatus.REJECTED):
            raise ValueError(
                f"A payroll run for {month}/{year} already exists with status {existing.status}. "
                "It cannot be reprocessed from here."
            )

        preview = self.build_preview(month, year)
        if preview["employee_count"] == 0:
            raise ValueError("No active salary assignments found to process payroll for.")

        now = datetime.now(timezone.utc)
        run = existing or PayrollRun(month=month, year=year, status=PayrollRunStatus.DRAFT, created_at=now, updated_at=now)
        if existing:
            for item in list(existing.items):
                self.db.delete(item)

        run.generated_by = generated_by
        run.status = PayrollRunStatus.PENDING_APPROVAL
        run.updated_at = now
        self.db.add(run)
        self.db.flush()

        for row in preview["rows"]:
            self.db.add(
                PayrollRunItem(
                    payroll_run_id=run.id,
                    employee_id=UUID(row["employee_id"]),
                    gross_salary=row["gross_salary"],
                    deductions=row["deductions"],
                    lop_days=row["lop_days"],
                    net_salary=row["net_salary"],
                    bank_account_number=row["bank_account_number"] or "",
                    ifsc_code=row["ifsc_code"] or "",
                    created_at=now,
                    updated_at=now,
                )
            )

        self.db.add(
            AuditLog(
                entity_type="payroll_run",
                entity_id=run.id,
                action="payroll_run.requested",
                old_value=None,
                new_value={"month": month, "year": year, "employee_count": preview["employee_count"], "total_net": preview["total_net_display"]},
                performed_by=generated_by,
            )
        )
        self.db.flush()
        return run

    def approve_payroll_run(self, run_id: UUID, approved_by: UUID | None) -> dict[str, Any]:
        run = self.db.scalar(
            select(PayrollRun).where(PayrollRun.id == run_id).options(selectinload(PayrollRun.items))
        )
        if not run:
            raise LookupError("Payroll run not found")
        if run.status == PayrollRunStatus.APPROVED:
            return self.run_summary(run)

        run.status = PayrollRunStatus.APPROVED
        run.approved_by = approved_by
        run.approved_at = datetime.now(timezone.utc)
        run.updated_at = datetime.now(timezone.utc)
        self.db.add(run)
        self.db.add(
            AuditLog(
                entity_type="payroll_run",
                entity_id=run.id,
                action="payroll_run.approved",
                old_value=None,
                new_value=self.run_summary(run),
                performed_by=approved_by,
            )
        )
        self.db.flush()
        return self.run_summary(run)

    # NEW: there was previously no way to reject a payroll run at all — the
    # ".reject" handler key for "payroll.process" fell through to the
    # governance placeholder, which never touches PayrollRun.status. A
    # rejected run would sit at PENDING_APPROVAL forever with no path back
    # to DRAFT for correction and resubmission.
    #
    # Mirrors approve_payroll_run's pattern: idempotent on repeat rejection,
    # writes an AuditLog, returns run_summary. Blocks rejecting a run that has
    # already moved past the approval gate (APPROVED / BANK_SHEET_GENERATED /
    # COMPLETED) since rejection only makes sense before that point — this is
    # a design assumption, flag if a different behavior is wanted.
    #
    # There is no `rejected_by` / `rejected_at` column on PayrollRun (only
    # approved_by/approved_at exist), so this does not add one to avoid a
    # migration; the rejecting user and reason are recorded on the AuditLog
    # entry instead, and `run.approved_by` is left untouched.
    def reject_payroll_run(self, run_id: UUID, rejected_by: UUID | None, reason: str | None = None) -> dict[str, Any]:
        run = self.db.scalar(
            select(PayrollRun).where(PayrollRun.id == run_id).options(selectinload(PayrollRun.items))
        )
        if not run:
            raise LookupError("Payroll run not found")
        if run.status == PayrollRunStatus.REJECTED:
            return self.run_summary(run)
        if run.status not in (PayrollRunStatus.PENDING_APPROVAL,):
            raise ValueError(
                f"Payroll run for {run.month}/{run.year} cannot be rejected from status {run.status}. "
                "Only a run awaiting approval can be rejected."
            )

        run.status = PayrollRunStatus.REJECTED
        run.updated_at = datetime.now(timezone.utc)
        self.db.add(run)
        self.db.add(
            AuditLog(
                entity_type="payroll_run",
                entity_id=run.id,
                action="payroll_run.rejected",
                old_value=None,
                new_value={**self.run_summary(run), "reason": reason},
                performed_by=rejected_by,
            )
        )
        self.db.flush()
        return self.run_summary(run)

    def generate_bank_sheet(self, run_id: UUID, approved_by: UUID | None) -> dict[str, Any]:
        run = self.db.scalar(
            select(PayrollRun)
            .where(PayrollRun.id == run_id)
            .options(selectinload(PayrollRun.items).selectinload(PayrollRunItem.employee))
        )
        if not run:
            raise LookupError("Payroll run not found")
        if run.status not in (PayrollRunStatus.APPROVED, PayrollRunStatus.BANK_SHEET_GENERATED):
            raise ValueError(
                f"Payroll run must be approved before generating a bank sheet (current status: {run.status})."
            )

        sheet_rows = [
            {
                "employee_id": str(item.employee_id),
                "employee_name": employee_display_name(item.employee) if item.employee else None,
                "bank_account_number": item.bank_account_number,
                "ifsc_code": item.ifsc_code,
                "net_salary": float(item.net_salary),
                "net_salary_display": money(item.net_salary),
            }
            for item in run.items
        ]

        run.status = PayrollRunStatus.BANK_SHEET_GENERATED
        run.updated_at = datetime.now(timezone.utc)
        self.db.add(run)

        total_amount = sum((Decimal(str(row["net_salary"])) for row in sheet_rows), Decimal("0"))
        self.db.add(
            AuditLog(
                entity_type="payroll_run",
                entity_id=run.id,
                action="payroll_run.bank_sheet_generated",
                old_value=None,
                new_value={"row_count": len(sheet_rows), "total_amount": money(total_amount)},
                performed_by=approved_by,
            )
        )
        self.db.flush()
        return {
            "run": self.run_summary(run),
            "bank_sheet": sheet_rows,
            "total_amount": float(total_amount),
            "total_amount_display": money(total_amount),
        }

    def run_summary(self, run: PayrollRun) -> dict[str, Any]:
        return {
            "id": str(run.id),
            "month": run.month,
            "year": run.year,
            "status": str(run.status),
            "employee_count": len(run.items) if run.items is not None else None,
            "generated_by": str(run.generated_by) if run.generated_by else None,
            "approved_by": str(run.approved_by) if run.approved_by else None,
            "approved_at": run.approved_at.isoformat() if run.approved_at else None,
        }