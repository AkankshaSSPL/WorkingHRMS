# Agentic HRMS — Engineering Audit

A code-level review of the HRMS platform (backend + frontend, full tree).

**Reviewed at commit:** `5d9eeb7`
**Method:** static read of the source. Every finding below cites the file and line it was read from. Nothing here is inferred or assumed.

| Critical | High | Medium | Modules working |
| --- | --- | --- | --- |
| 9 | 8 | 7 | 6 |

---

## The short version

The employee, leave, attendance and salary-configuration modules are genuinely built. Payroll, offboarding and notifications are not — and several of them **report success anyway**, so the gap is invisible from the UI.

Three things matter more than the rest:

1. **The leave-to-payroll calculation is wrong in a way that costs employees money.** Approved paid leave is charged as loss of pay.
2. **There is no ownership check anywhere in the system.** Any logged-in user can read every employee's Aadhaar, PAN and bank account, and can rewrite anyone's attendance.
3. **Payroll and offboarding don't exist**, but the agent returns `"execution_status": "Completed"` when you ask it to run them.

---

## Correctness — bugs that move money

These are not missing features. This is shipped logic producing wrong numbers.

### C1 — Approved paid leave is charged as loss of pay `CRITICAL`

The LOP calculator classifies leave against a **hardcoded list of leave-type names** and never reads the `leave_types` table:

```python
PAID_LEAVE_TYPES   = {"CASUAL LEAVE", "SICK LEAVE", "EARNED LEAVE", "CL", "SL", "EL"}
UNPAID_LEAVE_TYPES = {"UNPAID LEAVE", "LOP", "UL", "LOSS OF PAY"}
```

But the leave type the application actually creates and books against is **`"Paid Leave"`** — which is not in that list. It matches neither bucket, so `paid_leave_days` stays `0` and the day falls through into `lop_days`.

**Impact:** an employee takes approved paid leave, and payroll deducts it as unpaid. Every custom or renamed leave type hits the same bug. This flows straight into the salary deduction: `(gross / working_days) × lop_days`.

**Evidence:** `backend/app/services/lop_calculator.py:35-36, 75-87` · `agents/leave_agent/tools/core.py:18, 103-115` · `agents/salary_assignment_agent/api.py:95`

### C2 — A leave spanning two months is deducted twice `CRITICAL`

In the same loop, the calculator takes `days = float(leave.total_days)` — the **entire request's** length — but the query only tests whether the leave *overlaps* the month being run. A 6-day leave crossing a month boundary is counted as 6 days in **both** months.

**Evidence:** `backend/app/services/lop_calculator.py:56, 76`

### C3 — Approving leave silently fails to deduct the balance `CRITICAL`

Approval looks the leave type up **by name** and filters on `active = true`, ignoring the `leave_type_id` foreign key sitting on the row. If the type has been deactivated or renamed, the lookup returns `None`, **neither branch runs**, and the request is marked `APPROVED` with no balance deducted.

This is not hypothetical: migration `20260611_0019` **deactivates `Sick Leave`**. Every existing sick-leave request now approves for free.

**Evidence:** `agents/leave_agent/tools/core.py:260-264, 448-450` · `alembic/versions/20260611_0019_leave_entitlement_24_days.py:27-33`

### C4 — Approve and cancel disagree, inventing leave days `CRITICAL`

Approval branches on the leave type's `category` field. Cancellation branches on **hardcoded strings** (`if request.leave_type != "Unpaid Leave"`). An unpaid type not literally named *"Unpaid Leave"* is therefore never debited on approval — but **is** credited back on cancellation.

**Impact:** apply → approve → cancel, and the employee ends up with *more* leave balance than they started with. Repeatable.

**Evidence:** `agents/leave_agent/tools/core.py:261-264` (approve) · `core.py:296-300, 528-543` (cancel)

### C5 — Missing attendance is treated as "present" `CRITICAL`

When no attendance record exists for a working day, the system fabricates one with status `PRESENT` rather than flagging it as missing. Absence of evidence becomes evidence of presence — and it feeds `payable_days` and the payroll-impact endpoint.

**Evidence:** `agents/attendance_agent/tools.py:413, 443-457`

### C6 — Two people approving the same leave both deduct it `HIGH`

Balance updates are a read-modify-write in Python (`balance.used = float(balance.used) + days`) with no row lock and no atomic SQL update. Under the default READ COMMITTED isolation, two concurrent approvals both read the pre-update value and both write — one deduction is lost.

**Evidence:** `agents/leave_agent/tools/core.py:522-525` · `db/session.py:8`

---

## Security & access control

The permission system checks *what kind* of thing you may touch, never *which one*. That single omission drives most of what follows.

### S1 — No ownership check exists anywhere in the codebase `CRITICAL`

The logged-in user is referenced in ~20 places, and in **every one** it is used only to stamp an audit trail (`performed_by`, `requested_by`). It never appears in a `WHERE` clause. A user holding the base *Employee* role can:

- **File leave on behalf of any other employee** — `employee_id` is taken from the request body and never compared to the caller.
- **Read every employee's documents**, and attach documents to anyone's record.
- A *Manager* can read the leave history and balances of **any** employee, not just their reports.

**Evidence:** `agents/leave_agent/api.py:30, 66-89` · `api/v1/endpoints/documents.py:39-79` · `agents/salary_assignment_agent/api.py:33, 67`

### S2 — Every attendance endpoint is ungated, including the one that writes `CRITICAL`

All six attendance routes require only *authentication*, with no permission check at all. `POST /attendance/actions` is a **mutating** endpoint: any logged-in user, at the lowest role, can rewrite **any employee's attendance status for any date** — which then feeds payroll.

**Evidence:** `agents/attendance_agent/api.py:32, 47, 52, 57, 62, 76`

### S3 — Aadhaar, PAN and bank details are plaintext and returned to every reader `CRITICAL`

No encryption exists anywhere in the backend (zero matches for encrypt/cipher/fernet). `aadhaar_number`, `pan_number`, `bank_account_number`, `ifsc_code` and `dob` are stored as plain strings and returned in full by the employee profile endpoint — gated only by `employees:view`, a permission both *Manager* and *HR Executive* hold.

The same payload is also copied verbatim into `audit_logs.old_value` / `new_value`, **duplicating the PII into a second table** on every edit.

**Impact:** this is regulated personal data. A single compromised low-privilege account exfiltrates the identity and banking details of the entire workforce.

**Evidence:** `models/employee/models.py:155-159` · `agents/employee_agent/tools.py:64-84, 212, 219` · `api/v1/endpoints/employees.py:113-118, 156-165`

### S4 — Write operations are authorised by read permissions `HIGH`

Of the 15 permissions defined, **14 end in `:view`**. Only `approvals:manage` is write-scoped. So every mutation in the product is gated by a permission whose name says "view":

| Operation | Gated by |
| --- | --- |
| `POST / PATCH / DELETE /employees` | `employees:view` |
| `POST / PUT / DELETE /payroll/components` | `payroll:view` |
| `POST / PATCH / DELETE /masters/{type}` | `settings:view` |
| `POST /documents`, `/leave/requests` | `documents:view`, `leave:view` |

Practical effect: anyone who can *see* the employee directory can **delete employees from it**. Both *Manager* and *HR Executive* hold `employees:view`.

**Evidence:** `services/auth_service.py:20-36, 57-69` · `endpoints/employees.py:121, 146, 173`

### S5 — Logging out does not end the session `HIGH`

Logout revokes the *refresh* token only. The access token carries a `jti`, but it is never stored or checked — so a stolen or logged-out access token stays valid for its full **60-minute** lifetime. The only real kill switch is deactivating the user.

**Evidence:** `api/deps.py:16-31` · `core/security.py:22` · `services/auth_service.py:128-134`

### S6 — No rate limiting on login `HIGH`

No throttling, no failed-attempt counter, no lockout field on the user model. `POST /auth/login` can be brute-forced at full speed. (Password hashing itself is correct — bcrypt with per-password salt.)

**Evidence:** `api/v1/endpoints/auth.py:27` · `services/auth_service.py:87-96`

### S7 — Multi-tenancy is decorative `HIGH`

Every one of the 34 tables carries a `tenant_id` column and index. It is **never written on insert and never filtered on in any query**. Every row is `NULL`, in one shared pool. The column provides no isolation whatsoever — if the product is sold as multi-tenant, it isn't one.

**Evidence:** `models/base.py:17` · grep across `app/`: no assignment, no filter

### S8 — Salary formulas are passed to `eval()` `MEDIUM`

Two code paths call `eval()` on user-supplied formula strings. A character allowlist blocks identifiers, so **remote code execution is not reachable** — but `*` is permitted, therefore `**` is too. A formula of `9**9**9**9` passes the filter and hangs the worker. Reachable by anyone with `payroll:view`.

**Evidence:** `agents/salary_assignment_agent/services/salary_assignment_service.py:41-44` · `agents/salary_structure_agent/service.py:205-207`

---

## Features that report success but do nothing

The most dangerous category, because it is invisible from the UI. These paths return `"execution_status": "Completed"` without touching the database.

### F1 — Payroll does not exist `CRITICAL`

The `payroll_runs` and `payroll_run_items` tables are defined, and **nothing in the codebase ever reads or writes them** — the only reference in the entire tree is an unused ORM back-reference. There is no payroll run, no salary computation, no payslip, no bank sheet. The `/payroll` routes are salary-component CRUD only.

**Evidence:** `models/payroll/models.py:41, 58` · sole reference: `models/employee/models.py:172`

### F2 — "Generate payroll" quietly shows a list of salary components instead `CRITICAL`

The coordinator routes *"process payroll"* to the payroll agent's `process` action. That action **is not in the agent's supported list**, so the classifier's fallback silently rewrites it:

```python
return action if action in self.supported_actions else "inspect"
```

The request degrades to `inspect`, which lists salary components — and the response is labelled **`execution_status: "Completed"`**. The user asked to run payroll and is told it succeeded.

**Evidence:** `agents/coordinator_agent/service.py:43-44` · `agents/payroll_agent/service.py:25, 95-96, 108`

### F3 — Offboarding and notifications fabricate a successful result `CRITICAL`

`_invoke_placeholder_agent` performs **no database work at all** and returns a hardcoded success envelope:

```python
"message": f"{agent_display_name} completed the requested operation.",
"execution_status": "Completed",
"workflow_status": "Completed",
```

Ask the system to offboard an employee and it reports done. Nothing happened.

**Evidence:** `agents/coordinator_agent/service.py:55-64, 75-86, 651-676`

### F4 — Three approval handlers are stubs that claim to have executed `HIGH`

`payroll.process`, `payroll.generate_bank_sheet` and `offboarding.start` are registered to a placeholder that returns *"Placeholder governance handler executed. No HRMS business mutation was performed."* — and are never overridden. A manager can approve a payroll run and nothing runs.

(`employee.*`, `leave.approve`, `onboarding.start` and `salary_assignment.*` **are** real.)

**Evidence:** `agents/approval_agent/handlers.py:22-45`

### F5 — The dashboard is entirely fake `HIGH`

The landing page after login makes **zero API calls**. The stat cards are hardcoded strings — the value of "Total Employees" is the literal text `"Live"`; "Payroll Pending" reads `"Pending"` with the detail line *"Payroll agent not enabled"*. All three panels below are permanent empty-states.

**Evidence:** `Frontend/src/pages/DashboardPage.tsx:21-37`

### F6 — Five navigation items lead to empty shells `MEDIUM`

`/candidates`, `/assets`, `/offboarding`, `/audit-logs` and `/settings` all render the same placeholder component — but are advertised as real entries in the sidebar.

**Evidence:** `Frontend/src/routes/router.tsx:43, 67, 71, 75, 83`

### F7 — The "multi-agent LangGraph" layer is dead code `MEDIUM`

Every LangGraph graph in the project is defined and **never imported**. Two factories literally `return None`. The system that actually runs is a hand-written keyword/regex router. This matters for planning: the AI orchestration described in the README is not the thing in production.

**Evidence:** `agents/leave_agent/graph.py:4-6` · `agents/attendance_agent/graph.py:4-6` · `agents/coordinator_agent/service.py:156` (the real path)

---

## Reliability & data integrity

### R1 — Approvals can be executed twice, creating duplicate records `HIGH`

When an approval is resumed, the business handler runs in **its own database session and commits independently**, then the approval row is committed *separately* afterwards. If the process dies between those two commits, the employee (or leave deduction) is already durably written while the approval still reads as resumable — **so it can be resumed again**. No handler has rollback handling; they only `close()`.

**Evidence:** `agents/approval_agent/service.py:181, 194` · `agents/onboarding_agent/handlers.py:23, 65`

### R2 — The leave screen makes one HTTP request per employee, and each one writes `HIGH`

The frontend fans out `Promise.all(employees.map(getEmployeeLeaveBalances))` — an N+1, over the network. Worse, that *GET* endpoint inserts default leave types and balances and calls `flush()`, but **never commits** — so `get_db()` rolls it all back on close. The same inserts are redone and thrown away on every page load. The page also silently covers only the first 50 employees.

**Evidence:** `Frontend/src/services/leave.ts:83` · `agents/leave_agent/tools/core.py:474, 509` · `Frontend/src/services/employees.ts:67`

### R3 — The leave schema carries four sets of duplicate columns `MEDIUM`

Parallel columns exist with no synchronisation between them. The dangerous pair: `leave_requests` stores both `start_date/end_date` and `from_date/to_date`. The **backend calculates on `start_date`**, but `from_date` is written once at creation and never updated again — while the **API payload and the UI both prefer `from_date`**. Edit a leave request and the UI shows the old dates while payroll acts on the new ones.

| Table | Duplicate pair | Authoritative |
| --- | --- | --- |
| `leave_requests` | `start_date/end_date` vs `from_date/to_date` | `start_date` — but the UI reads the other |
| `leave_requests` | `leave_type_id` (FK) vs `leave_type` (string) | the string — so renaming a type breaks lookups |
| `leave_types` | `annual_allocation` vs `annual_quota` | `annual_allocation`; the other is legacy |
| `leave_types` | `category` vs `is_paid` | `category`; `is_paid` is read by nothing |

**Evidence:** `models/employee/models.py:271-274, 290-300` · `leave_agent/tools/core.py:330-331` · `Frontend/src/pages/LeavePage.tsx:23-24`

### R4 — Two migrations destroy configured data and cannot be rolled back `MEDIUM`

`20260611_0019` overwrites every Casual and Paid Leave allocation to `12`, wiping any customised entitlement — and its `downgrade()` restores only Sick Leave, not the two it changed. `20260611_0020` zeroes and recomputes balances with `downgrade(): pass`.

Note also that the migration named **`leave_entitlement_24_days` actually writes 12**.

**Evidence:** `alembic/versions/20260611_0019_...py:20-43, 53-70` · `20260611_0020_...py:80-81`

### R5 — Twelve list endpoints return the entire table `MEDIUM`

Only two endpoints paginate with a cap. The rest — pending approvals, leave calendar, masters, lookups, payroll components, documents, workflows, and `/employees/form-options` (which loads *every* employee as a manager option) — have no limit at all. These degrade linearly with headcount.

### R6 — Six screens render an API failure as "no data" `MEDIUM`

Documents, Masters, Onboarding, Agent Command, the Attendance dashboard and the employee profile drawer all handle `isLoading` but never `isError`. When the backend is down, the user sees an empty register and `0 / 0 / 0` metric cards — indistinguishable from a genuinely empty system.

(Employees, Approvals, Leave and Payroll **do** handle it correctly.)

### R7 — Soft-deleted employees remain fully operable `MEDIUM`

Several paths fetch by primary key and check only for `None`, ignoring the `deleted_at` marker. You can still post attendance for a deleted employee, read their salary, and approve a deleted leave request — *which then consumes leave balance*.

**Evidence:** `agents/attendance_agent/api.py:70, 80` · `agents/salary_assignment_agent/api.py:38, 75` · `leave_agent/tools/core.py:246, 272, 289`

---

## Engineering practice

### E1 — There are no tests, and no CI `HIGH`

Zero test files exist in the repository — no pytest, no vitest, no `conftest.py`, no test config of any kind. There is no `.github/` directory and no pipeline. The only executable checks are three `smoke_*.py` scripts that must be run by hand and assert nothing.

**Why this compounds everything above:** every bug in this report is the kind a single unit test would have caught. Fixing them without a test suite means fixing them blind, and there is no guard against re-introducing them.

### E2 — The Docker setup cannot work `MEDIUM`

The backend `Dockerfile` starts uvicorn on port **8001**, while `docker-compose.yml` maps `8000:8000` — so the published port points at nothing listening. The compose file also defines no frontend service at all, and its Postgres password (`secret`) does not match the one in the sample env.

**Evidence:** `backend/Dockerfile:10` · `infrastructure/docker-compose.yml:22, 29-30`

### E3 — Errors are swallowed in ways that corrupt results `MEDIUM`

Two stand out. A failed formula evaluation returns `0.0` — **silently computing a salary component as zero**. A failed duplicate-check returns "no duplicate" and proceeds with the insert.

```python
except Exception:
    return 0.0          # a salary component, on any formula error
```

**Evidence:** `agents/salary_structure_agent/service.py:92-94, 208-209`

### E4 — Real employee names and salaries are hardcoded into a script `MEDIUM`

`cleanup_onboarding_data.py` hardcodes three named individuals with their departments, managers and salaries (₹80,000 / ₹45,000 / ₹100,000), then mass-soft-deletes records it judges duplicates by display name — with no dry-run and no confirmation. A real employee's name is also baked into a user-facing prompt string in the employee agent.

**Evidence:** `backend/scripts/cleanup_onboarding_data.py:135-155` · `agents/employee_agent/service.py:104`

### E5 — Dates use server-local time, and some defaults freeze at boot `MEDIUM`

All business-date decisions call `date.today()` — server local time, with no configured business timezone. Two query defaults are evaluated **at module import**, so the default payroll month/year is pinned to the day the server started and goes stale. Weekends are hardcoded as Sat/Sun with no week-off configuration, and there is no holiday calendar table.

**Evidence:** `agents/salary_assignment_agent/api.py:70-71` · `leave_agent/tools/core.py:617-619`

---

## What actually works

This is a real system, and the report should not obscure that. The following are genuinely implemented, wired end to end, and functioning:

- **Employee CRUD** — list, search, create, update, soft-delete, with audit logging.
- **Onboarding** — the approval handler really does create the employee, asset, notification and audit records, atomically.
- **Leave** — apply, approve, reject, cancel, balances, calendar. (The correctness bugs above are in the *calculation*, not the plumbing.)
- **Attendance** — matrix, calendar, dashboard, cell editing.
- **Salary configuration** — components, structures, assignments with approval flow.
- **Auth & RBAC** — bcrypt hashing, JWT with correct single-use refresh-token rotation, permission-gated routes and UI.
- **The approval engine** — for employee, leave, salary and onboarding it is real, with full audit and event trails.

Also worth stating: there are **no SQL-injection vectors**, no path-traversal in the file upload, CORS is correctly restricted, and **no secret has ever been committed to git** — `.gitignore` caught the API key. Every frontend call resolves to a real backend route; there are no broken contracts.

---

## Suggested order of work

Sequenced by damage-per-hour, not by difficulty.

| # | Action | Addresses | Size |
| --- | --- | --- | --- |
| 1 | Make the fake "Completed" responses fail loudly instead — unsupported actions should raise, not degrade to `inspect` | F2, F3, F4 | Hours |
| 2 | Drive leave classification off `leave_types.category` / the FK, not hardcoded name lists | C1, C3, C4 | Days |
| 3 | Add an ownership check + split write permissions from `:view`; gate the attendance routes | S1, S2, S4 | Days |
| 4 | Stop returning Aadhaar/PAN/bank in the profile payload; stop copying them into audit rows | S3 | Days |
| 5 | Stand up a test suite around leave & LOP before touching anything else in payroll | E1 | Days |
| 6 | Decide: build payroll, or remove it from the UI and the pitch | F1, F5 | Weeks |
