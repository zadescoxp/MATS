from framework.core.context import AgentContext
from framework.core.state import GraphState
from framework.schemas.models import ExecutionReport


class ExecutionNode:
    def __init__(self, context: AgentContext):
        self.context = context

    def __call__(self, state: GraphState) -> dict[str, ExecutionReport]:
        if not state.critique or state.critique.decision != "APPROVE":
            raise ValueError("Execution requires Critique approval")
        if not state.risk_assessment:
            raise ValueError("Execution requires a sized risk plan")
        dry_run = (
            self.context.settings.dry_run
            or self.context.settings.kill_switch
            or not state.risk_assessment.approved
        )
        if not state.risk_assessment.approved:
            report = ExecutionReport(
                status="dry_run",
                orders=[],
                fills=[],
                errors=state.risk_assessment.account_constraints,
                dry_run=True,
            )
        else:
            outcome = self.context.execution.place_order(
                state.symbol,
                "buy",
                state.risk_assessment.position_size,
                "market",
                dry_run=dry_run,
                idempotency_key=f"{state.run_id}:spot-entry",
            )
            status = outcome["status"]
            report = ExecutionReport(
                status=status,
                orders=outcome.get("orders", [outcome.get("order", {})]),
                fills=outcome.get("fills", []),
                errors=outcome.get("errors", []),
                dry_run=status == "dry_run",
            )

        self.context.database.save_execution(
            f"{state.run_id}-execution",
            state.run_id,
            report.model_dump(mode="json")["orders"],
            report.status,
        )
        return {"execution_result": report, "status": "completed"}
