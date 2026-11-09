from mirador.application.orchestration.investigator import Investigator
from mirador.application.use_cases.triggers import trigger_from_question
from mirador.domain.budget import BudgetExceeded
from mirador.domain.report import Report


class AskUseCase:
    def __init__(self, investigator: Investigator) -> None:
        self.investigator = investigator

    async def execute(
        self, question: str, thread: str | None, *, investigation_id: str | None = None
    ) -> Report:
        inv = self.investigator
        if (
            await inv.store.monthly_spend(
                inv.clock.now().date().replace(day=1), "investigation"
            )
            >= inv.config.budgets.monthly_usd
        ):
            raise BudgetExceeded("monthly_usd")
        trigger = trigger_from_question(question, inv.redactor, inv.clock)
        return await inv.run(
            trigger,
            parent_id=thread,
            spend_kind="investigation",
            investigation_id=investigation_id,
        )
