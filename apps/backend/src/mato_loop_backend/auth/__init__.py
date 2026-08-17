from mato_loop_backend.auth.accounts import (
    ExternalAccountUnavailableError,
    ExternalIdentityConflictError,
    FinalLoginMethodError,
    link_external_identity,
    resolve_external_identity,
    unlink_external_identity,
)
from mato_loop_backend.auth.identity import AuthenticatedIdentity
from mato_loop_backend.auth.local import LocalAuthProvider

__all__ = [
    "AuthenticatedIdentity",
    "ExternalAccountUnavailableError",
    "ExternalIdentityConflictError",
    "FinalLoginMethodError",
    "LocalAuthProvider",
    "link_external_identity",
    "resolve_external_identity",
    "unlink_external_identity",
]
