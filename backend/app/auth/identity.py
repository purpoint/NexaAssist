"""Who a request belongs to.

One value type for both cases, rather than ``RequestIdentity | None``. A
caller that has to check for ``None`` before asking anything eventually
forgets, and the forgetful path is the one that treats an anonymous request as
a privileged one. An unauthenticated request still has an identity here -- it
is simply an anonymous one, and says so.

The subject is a stable, non-secret label. It is what authorization compares
against and what appears in logs, so it must never be the credential itself.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

ANONYMOUS_SUBJECT = "anonymous"


class IdentityKind(StrEnum):
    """How the identity was established."""

    ANONYMOUS = "anonymous"
    API_KEY = "api_key"


class RequestIdentity(BaseModel):
    """The principal behind one request."""

    model_config = ConfigDict(frozen=True)

    subject: str = Field(
        min_length=1,
        description=(
            "Stable, non-secret label for the principal. Compared by "
            "authorization and safe to log; never the credential."
        ),
    )
    kind: IdentityKind
    authenticated: bool = Field(
        description="False for anonymous requests, which are still identities."
    )

    @classmethod
    def anonymous(cls) -> "RequestIdentity":
        return cls(
            subject=ANONYMOUS_SUBJECT,
            kind=IdentityKind.ANONYMOUS,
            authenticated=False,
        )

    @classmethod
    def api_key(cls, subject: str) -> "RequestIdentity":
        return cls(subject=subject, kind=IdentityKind.API_KEY, authenticated=True)
