"""Exception hierarchy. Messages must never contain secrets."""


class StratosError(Exception):
    """Base class for all expected Stratos failures."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class AuthenticationError(StratosError): ...


class AuthorizationError(StratosError): ...


class ValidationError(StratosError): ...


class APIError(StratosError): ...


class NetworkError(StratosError): ...


class ConfigurationError(StratosError): ...


class ResourceNotFoundError(StratosError): ...


class DependencyError(StratosError): ...


class OperationCancelledError(StratosError): ...


__all__ = [
    "APIError",
    "AuthenticationError",
    "AuthorizationError",
    "ConfigurationError",
    "DependencyError",
    "NetworkError",
    "OperationCancelledError",
    "ResourceNotFoundError",
    "StratosError",
    "ValidationError",
]
