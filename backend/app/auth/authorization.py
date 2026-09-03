"""Who may see what.

Authentication established *who* is asking; this decides what that answer
entitles them to. Kept separate because the two fail differently: a request
with no identity is a 401 and should say so, while a request for somebody
else's resource must be indistinguishable from a request for a resource that
does not exist.

That last rule is the design. Returning 403 for a resource you do not own
confirms it exists, which is exactly the fact being protected -- so an
ownership failure raises the resource's own not-found error and the difference
is recorded only in the log.

Ownership is scoped by *subject*, the non-secret label an identity carries. A
resource stamped with a subject belongs to it.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.auth.identity import RequestIdentity


@dataclass(frozen=True)
class OwnerScope:
    """A restriction to one subject's resources.

    Passed into a service as an optional argument, so every existing caller --
    and every deployment that does not scope -- keeps working by passing
    nothing. The permission rule lives here rather than in each service, so
    there is one definition of "owns".
    """

    subject: str

    def permits(self, owner: str | None) -> bool:
        """Whether this scope may see a resource with that owner.

        An unowned resource is refused, not shared. Rows created before
        ownership existed, or while authentication was off, carry no owner --
        and letting any authenticated subject read all of them would be a
        worse outcome than losing access to them. Fail closed.
        """
        return owner is not None and owner == self.subject


@runtime_checkable
class Authorizer(Protocol):
    """Decides whether ownership is enforced, and for whom."""

    name: str

    @property
    def scopes(self) -> bool:
        """Whether this authorizer restricts anything."""
        ...

    def scope_for(self, identity: RequestIdentity) -> OwnerScope | None:
        """The scope to apply to reads, or ``None`` for no restriction."""
        ...

    def owner_for(self, identity: RequestIdentity) -> str | None:
        """The owner to stamp on a resource this identity creates."""
        ...


class OpenAuthorizer:
    """Enforces nothing.

    The default, and the reason every endpoint behaves exactly as it did
    before this milestone. Not a stub: a single-tenant deployment genuinely
    has no ownership to enforce, and expressing that as an implementation
    means no service needs a branch for it.
    """

    name = "open"

    @property
    def scopes(self) -> bool:
        return False

    def scope_for(self, identity: RequestIdentity) -> OwnerScope | None:
        return None

    def owner_for(self, identity: RequestIdentity) -> str | None:
        # Nothing is stamped, so turning scoping on later does not
        # retroactively hand old rows to whoever happens to share a subject.
        return None


class SubjectScopedAuthorizer:
    """Restricts every resource to the subject that created it."""

    name = "subject"

    @property
    def scopes(self) -> bool:
        return True

    def scope_for(self, identity: RequestIdentity) -> OwnerScope | None:
        return OwnerScope(subject=identity.subject)

    def owner_for(self, identity: RequestIdentity) -> str | None:
        return identity.subject
