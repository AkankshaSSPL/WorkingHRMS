"""Unit tests for EmployeeAgent._status_response.

These tests target the outcome-mapping fix: `_status_response()` used to
silently default every non-happy-path response to "Completed" regardless of
whether the operation actually succeeded. It now requires an explicit
`outcome` ("completed" | "failed" | "needs_input") and maps it through
`_OUTCOME_STATUS_LABELS` to the exact string the frontend badge keys off.

No database session is needed: `_status_response` never touches `self.db`,
so `EmployeeAgent(db=None)` is safe to instantiate directly here.
"""

import pytest

from app.agents.employee_agent.service import EmployeeAgent, _OUTCOME_STATUS_LABELS


@pytest.fixture
def agent() -> EmployeeAgent:
    return EmployeeAgent(db=None)


class TestStatusResponseOutcomeMapping:
    """Each recognized outcome must map to the exact expected badge label,
    on both execution_status/workflow_status and structured_response."""

    def test_completed_outcome_maps_to_completed_label(self, agent: EmployeeAgent) -> None:
        result = agent._status_response("Update cancelled", "No changes were made.", outcome="completed")

        assert result["execution_status"] == "Completed"
        assert result["workflow_status"] == "Completed"
        assert result["structured_response"]["outcome"] == "completed"

    def test_failed_outcome_maps_to_failed_label(self, agent: EmployeeAgent) -> None:
        result = agent._status_response("Employee not found", "No matching record.", outcome="failed")

        assert result["execution_status"] == "Failed"
        assert result["workflow_status"] == "Failed"
        assert result["structured_response"]["outcome"] == "failed"

    def test_needs_input_outcome_maps_to_needs_input_label(self, agent: EmployeeAgent) -> None:
        result = agent._status_response("Salary amount needed", "Please include an amount.", outcome="needs_input")

        assert result["execution_status"] == "Needs Input"
        assert result["workflow_status"] == "Needs Input"
        assert result["structured_response"]["outcome"] == "needs_input"


class TestStatusResponseValidation:
    def test_unrecognized_outcome_raises_value_error(self, agent: EmployeeAgent) -> None:
        with pytest.raises(ValueError, match="Unknown status_response outcome"):
            agent._status_response("Some title", "Some summary", outcome="success")

    def test_outcome_is_required_in_practice_not_silently_completed(self, agent: EmployeeAgent) -> None:
        """Regression guard for the original bug: calling with an outcome
        that isn't 'completed' must never come back labeled Completed."""
        result = agent._status_response("Employee not found", "No matching record.", outcome="failed")

        assert result["execution_status"] != "Completed"
        assert result["structured_response"]["outcome"] != "completed"


class TestStatusResponseShape:
    def test_response_carries_title_and_summary_through(self, agent: EmployeeAgent) -> None:
        result = agent._status_response("My Title", "My summary text.", outcome="failed")

        assert result["operation_summary"] == "My Title"
        assert result["message"] == "My summary text."
        assert result["execution_summary"] == "My summary text."
        assert result["structured_response"]["type"] == "status_banner"
        assert result["structured_response"]["title"] == "My Title"
        assert result["structured_response"]["summary"] == "My summary text."

    def test_agent_identity_fields_are_set(self, agent: EmployeeAgent) -> None:
        result = agent._status_response("Title", "Summary", outcome="completed")

        assert result["agent"] == "employee_agent"
        assert result["agent_display_name"] == "Employee Agent"
        assert result["action"] == "status"


class TestOutcomeStatusLabelsMapping:
    """Guards the mapping table itself, so a future edit can't quietly drop
    or rename one of the three recognized outcomes."""

    def test_all_three_outcomes_are_present(self) -> None:
        assert set(_OUTCOME_STATUS_LABELS.keys()) == {"completed", "failed", "needs_input"}

    def test_label_values_match_frontend_expected_strings(self) -> None:
        assert _OUTCOME_STATUS_LABELS["completed"] == "Completed"
        assert _OUTCOME_STATUS_LABELS["failed"] == "Failed"
        assert _OUTCOME_STATUS_LABELS["needs_input"] == "Needs Input"