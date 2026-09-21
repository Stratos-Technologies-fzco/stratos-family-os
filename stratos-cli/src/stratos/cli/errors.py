"""Exception -> exit code / message mapping (single source of truth)."""

import traceback

from stratos.domain.enums import ExitCode
from stratos.domain.exceptions import (
    APIError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DependencyError,
    NetworkError,
    OperationCancelledError,
    ResourceNotFoundError,
    StratosError,
    ValidationError,
)
from stratos.domain.interfaces import Renderer
from stratos.utils.redaction import SecretRedactor

# Order does not matter: lookup is by exact type walking the MRO.
_EXIT_CODES: dict[type[BaseException], ExitCode] = {
    StratosError: ExitCode.GENERAL_ERROR,
    AuthenticationError: ExitCode.AUTHENTICATION,
    AuthorizationError: ExitCode.AUTHORIZATION,
    ResourceNotFoundError: ExitCode.NOT_FOUND,
    ValidationError: ExitCode.VALIDATION,
    NetworkError: ExitCode.NETWORK,
    APIError: ExitCode.NETWORK,
    ConfigurationError: ExitCode.CONFIGURATION,
    DependencyError: ExitCode.DEPENDENCY,
    OperationCancelledError: ExitCode.CANCELLED,
}


def exit_code_for(exc: BaseException) -> ExitCode:
    for cls in type(exc).__mro__:
        if cls in _EXIT_CODES:
            return _EXIT_CODES[cls]
    return ExitCode.GENERAL_ERROR


def handle_error(
    exc: BaseException,
    renderer: Renderer,
    *,
    debug: bool = False,
    redactor: SecretRedactor | None = None,
) -> ExitCode:
    """Render a user-friendly, redacted message and return the exit code.
    Tracebacks are shown only with --debug."""
    redactor = redactor or SecretRedactor()
    if isinstance(exc, StratosError):
        message, hint = exc.message, exc.hint
    else:
        message, hint = f"Unexpected error: {exc}", "Re-run with --debug for details."
    renderer.error(redactor.redact_text(message), hint=hint)
    if debug:
        renderer.debug("".join(traceback.format_exception(exc)))
    return exit_code_for(exc)
