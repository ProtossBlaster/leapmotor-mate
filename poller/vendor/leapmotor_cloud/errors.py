"""Errors deliberately avoid including caller data or credentials."""


class ValidationError(ValueError):
    """An input violates the offline API contract."""


class CommandUnavailable(RuntimeError):
    """A command cannot be prepared under the supplied capability decision."""

    def __init__(self, decision):
        self.decision = decision
        super().__init__(f"Command unavailable: {decision.state.value}/{decision.reason}")

