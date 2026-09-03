"""What an authenticator is.

A ``Protocol`` for the usual reason: the mechanism has to be replaceable
without every caller changing. Today it is a shared key; a signed token or an
identity provider is a second implementation, not a refactor.

``authenticate`` is async even though the shipped implementations need no
await. A real verifier fetches keys or calls an introspection endpoint, and a
protocol that could not accommodate that would force the refactor it exists to
prevent.

Credentials arrive as an opaque string -- whatever a transport extracted --
because the authenticator's job is to judge them, not to know about headers.
"""

from typing import Protocol, runtime_checkable

from app.auth.identity import RequestIdentity


@runtime_checkable
class Authenticator(Protocol):
    """Turns presented credentials into an identity, or refuses."""

    name: str

    @property
    def protects(self) -> bool:
        """Whether this authenticator actually rejects anything.

        Lets a caller distinguish "authentication is off" from "authentication
        is on and this request passed", without inspecting settings.
        """
        ...

    async def authenticate(self, presented: str | None) -> RequestIdentity:
        """Return the identity for ``presented``.

        Raises :class:`~app.auth.errors.AuthenticationError` when credentials
        are required and absent or wrong. Never returns ``None``: an
        unauthenticated request gets an anonymous identity only where that is
        the configured intent.
        """
        ...
