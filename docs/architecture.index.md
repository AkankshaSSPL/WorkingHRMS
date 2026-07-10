# Architecture Index — where to look

Cheat-sheet for [`architecture.json`](architecture.json). Use it two ways:

- **Big-context tool (ChatGPT):** paste the whole `architecture.json`, then your request.
- **Small-context tool (DeepSeek):** find your task below, open `architecture.json`, and paste **only**
  the JSON section(s) named in the "JSON path" column, plus `$meta` and the relevant `playbooks.*`.

Always tell the tool: *"Only touch the files named in that section. Match the shown pattern. Cite
`file:line`. After coding, update `docs/architecture.json` per its `update_protocol`."*

---

## "I want to change / debug X" → look here

| Task | JSON path(s) to paste | Playbook |
|---|---|---|
| Add/modify a REST endpoint | `backend.api`, `backend.api.deps`, `cross_cutting.permissions_catalog` | `playbooks.add_backend_endpoint` |
| Add a DB table/column | `backend.models`, `backend.db`, `backend.migrations` | `playbooks.add_model_and_migration` |
| Add/modify an AI agent | `backend.agents`, `backend.approvals` | `playbooks.add_new_agent`, `playbooks.add_agent_action` |
| Change approvals / human-in-the-loop | `backend.approvals`, `architecture_overview.approval_governance_flow` | `playbooks.trace_an_agent_command_end_to_end` |
| Add a frontend page/route | `frontend.routing`, `frontend.pages`, `frontend.components`, `cross_cutting.permissions_catalog` | `playbooks.add_frontend_page` |
| Call a backend endpoint from the UI | `frontend.services`, `frontend.conventions` | `playbooks.wire_frontend_to_new_endpoint` |
| Auth / login / JWT / RBAC | `architecture_overview.auth_flow`, `backend.core`, `backend.services.auth_service`, `frontend.stores` | `playbooks.debug_auth_401` |
| Permissions & roles | `backend.services.auth_service.constants`, `cross_cutting.permissions_catalog` | — |
| A 500 error | `backend.api`, `backend.models`, `backend.services` | `playbooks.debug_backend_500` |
| A migration won't run | `backend.migrations` | `playbooks.debug_migration_failure` |
| Setup / run / env / ports | `runbook` | — |
| "Why is it behaving weird?" | `cross_cutting.known_issues_and_gotchas` | — |

---

## Fast facts

- **Backend:** FastAPI, all routes under `/api/v1` (entry `backend/app/main.py`, router
  `backend/app/api/v1/router.py`). Port **8000**.
- **Frontend:** React+Vite (`Frontend/`, entry `src/main.tsx`, routes `src/routes/router.tsx`,
  fetch client `src/services/api.ts`). Port **5173**.
- **DB:** Postgres, SQLAlchemy 2.0, Alembic head **`20260612_0024`**. Every table inherits
  `id, tenant_id, created_at, updated_at, deleted_at` (soft delete via `deleted_at`).
- **Agents:** LangGraph multi-agent; real orchestrator is
  `CoordinatorRuntimeService.submit_command` (`backend/app/agents/coordinator_agent/service.py`);
  intent from `backend/app/agents/shared/natural_language.py`; approvals via
  `backend/app/agents/approval_agent/`.
- **RBAC:** 15 permission codes in `backend/app/services/auth_service.py`; gate backend with
  `require_permissions('code')` (`backend/app/api/deps.py`) **and** frontend with
  `hasPermission('code')` — always change both.

## Keeping this in sync

After any code change, update `architecture.json` following its `update_protocol` block and bump
`$meta.last_synced_git_commit`. The exact JSON paths to touch for each kind of code change are listed
in `update_protocol.when_you_change_code_update_these_json_paths`. Paste
`update_protocol.paste_this_instruction_to_your_ai_tool` to your AI tool so it does this for you.
