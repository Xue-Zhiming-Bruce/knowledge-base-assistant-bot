"""Domain error taxonomy."""


class DomainError(Exception):
    """Base class for expected domain failures."""


class InvariantViolationError(DomainError, ValueError):
    """A domain object would violate a required invariant."""


class InvalidStateTransitionError(DomainError):
    """A workflow state transition is not allowed."""


class DocumentNotFoundError(DomainError):
    """The requested canonical document does not exist."""


class DocumentConflictError(DomainError):
    """The canonical document changed since it was last observed."""


class UnsafeVaultPathError(DomainError):
    """A requested vault path escapes or violates the managed namespace."""


class ProjectionRebuildRequiredError(DomainError):
    """Runtime models are incompatible with the active retrieval projection."""
