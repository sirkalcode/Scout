import pytest
from llm_gateway.ledger import InMemoryLedger

from ..ledger_contract import LedgerContract


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestInMemoryLedger(LedgerContract):
    @pytest.fixture
    def ledger(self) -> InMemoryLedger:
        return InMemoryLedger()
