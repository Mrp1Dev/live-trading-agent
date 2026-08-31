class ExecutionError(RuntimeError):
    """Base execution-layer error."""


class BrokerError(ExecutionError):
    """Alpaca API or broker communication failure."""


class MCPBrokerError(BrokerError):
    """MCP connection or tool-call failure (legacy compatibility)."""


class SafetyViolation(ExecutionError):
    """An order failed a deterministic safety check."""

