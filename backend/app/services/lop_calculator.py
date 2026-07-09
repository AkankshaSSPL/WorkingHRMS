from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import and_, extract, select
from sqlalchemy.orm import Session

from app.models.employee import AttendanceRecord, LeaveRequest
from app.models.employee.models import AttendanceStatus, LeaveRequestStatus


@dataclass(frozen=True)
class LOPResult:
    employee_id: str
    working_days: float
    present_days: float
    paid_leave_days: float
    unpaid_leave_days: float
    lop_days: float

    def as_dict(self) -> dict:
        return {
            "employee_id": self.employee_id,
            "working_days": self.working_days,
            "present_days": self.present_days,
            "paid_leave_days": self.paid_leave_days,
            "unpaid_leave_days": self.unpaid_leave_days,
            "lop_days": self.lop_days,
        }


PAID_LEAVE_TYPES = {"CASUAL LEAVE", "SICK LEAVE", "EARNED LEAVE", "CL", "SL", "EL"}
UNPAID_LEAVE_TYPES = {"UNPAID LEAVE", "LOP", "UL", "LOSS OF PAY"}


def calculate_lop(db: Session, *, employee_id: UUID, month: int, year: int) -> LOPResult:
    attendance = list(
        db.scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.employee_id == employee_id,
                extract("month", AttendanceRecord.attendance_date) == month,
                extract("year", AttendanceRecord.attendance_date) == year,
                AttendanceRecord.deleted_at.is_(None),
            )
        )
    )
    approved_leaves = list(
        db.scalars(
            select(LeaveRequest).where(
                LeaveRequest.employee_id == employee_id,
                LeaveRequest.status == LeaveRequestStatus.APPROVED,
                LeaveRequest.deleted_at.is_(None),
                and_(LeaveRequest.start_date <= date(year, month, calendar.monthrange(year, month)[1]), LeaveRequest.end_date >= date(year, month, 1)),
            )
        )
    )

    working_days = 0.0
    present_days = 0.0
    for record in attendance:
        status = str(record.status)
        if status in {AttendanceStatus.WEEKLY_OFF, AttendanceStatus.HOLIDAY}:
            continue
        working_days += 1
        if status in {AttendanceStatus.PRESENT, AttendanceStatus.WORK_FROM_HOME, AttendanceStatus.ON_DUTY}:
            present_days += 1
        elif status == AttendanceStatus.HALF_DAY:
            present_days += 0.5

    paid_leave_days = 0.0
    unpaid_leave_days = 0.0
    for leave in approved_leaves:
        days = float(leave.total_days or 0)
        leave_type = str(leave.leave_type or "").upper()
        if leave_type in UNPAID_LEAVE_TYPES:
            unpaid_leave_days += days
        elif leave_type in PAID_LEAVE_TYPES:
            paid_leave_days += days

    if not working_days:
        days_in_month = calendar.monthrange(year, month)[1]
        working_days = sum(1 for day in range(1, days_in_month + 1) if date(year, month, day).weekday() < 5)

    lop_days = max(0.0, working_days - present_days - paid_leave_days) + unpaid_leave_days
    return LOPResult(
        employee_id=str(employee_id),
        working_days=working_days,
        present_days=present_days,
        paid_leave_days=paid_leave_days,
        unpaid_leave_days=unpaid_leave_days,
        lop_days=round(lop_days, 2),
    )
