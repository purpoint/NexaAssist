"""The shipped authenticators.

Two, so the protocol is verified rather than asserted -- the same discipline
the LLM providers, embedders, and job queues follow.
"""

import hashlib
import secrets
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth.errors import (
    AuthenticationConfigurationError,
    AuthenticationRequiredError,
    InvalidCredentialsError,
)
from app.auth.identity import RequestIdentity
from app.core.logging import get_logger

logger = get_logger(__name__)

MIN_KEY_LENGTH = 16
"""Short keys are guessable, and a deployment that sets one should be told so."""


class ApiKeyCredential(BaseModel):
    """One configured key, and the subject it identifies."""

    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1)
    secret: SecretStr


class AnonymousAuthenticator:
    """Accepts everything as anonymous.

    The default, and the reason every endpoint keeps working unchanged when
    authentication is not configured. It is not a stub: "this deployment does
    not authenticate" is a real, chosen mode, and making it an implementation
    of the same protocol means no route needs a branch for it.
    """

    name = "none"

    @property
    def protects(self) -> bool:
        return False

    async def authenticate(self, presented: str | None) -> RequestIdentity:
        # A credential presented to a deployment that does not check them is
        # ignored rather than rejected: refusing it would break a client that
        # sends one harmlessly.
        return RequestIdentity.anonymous()


class ApiKeyAuthenticator:
    """Matches a presented key against the configured set."""

    name = "api_key"

    def __init__(self, credentials: Iterable[ApiKeyCredential]) -> None:
        self._credentials: tuple[ApiKeyCredential, ...] = tuple(credentials)
        if not self._credentials:
            raise AuthenticationConfigurationError(
                "The api_key authenticator requires at least one key.",
            )
        subjects = [credential.subject for credential in self._credentials]
        if len(set(subjects)) != len(subjects):
            raise AuthenticationConfigurationError(
                "Each subject may be configured only once.",
            )

    @property
    def protects(self) -> bool:
        return True

    @property
    def subjects(self) -> Sequence[str]:
        """Configured subjects. Labels only -- never the keys."""
        return tuple(credential.subject for credential in self._credentials)

    async def authenticate(self, presented: str | None) -> RequestIdentity:
        if not presented:
            # No fingerprint to log: there is nothing to fingerprint.
            logger.info("authentication failed reason=missing_credentials")
            raise AuthenticationRequiredError()

        match = self._match(presented)
        if match is None:
            # A short digest, not the key. Enough to correlate repeated
            # attempts from one bad client; useless to anyone reading the log.
            logger.warning(
                "authentication failed reason=invalid_credentials fingerprint=%s",
                fingerprint(presented),
            )
            raise InvalidCredentialsError()

        logger.info("authenticated subject=%s", match.subject)
        return RequestIdentity.api_key(match.subject)

    def _match(self, presented: str) -> ApiKeyCredential | None:
        """Compare against every key in constant time.

        ``compare_digest`` for each candidate, and no early exit on the first
        mismatch: returning as soon as a byte differs leaks, through timing,
        how much of a key was right.
        """
        found: ApiKeyCredential | None = None
        for credential in self._credentials:
            if secrets.compare_digest(presented, credential.secret.get_secret_value()):
                found = credential
        return found


def fingerprint(presented: str) -> str:
    """A short, one-way label for a credential.

    Never reversible to the key, and never long enough to be useful as one.
    """
    return hashlib.sha256(presented.encode("utf-8")).hexdigest()[:12]


def parse_credentials(entries: Iterable[str]) -> list[ApiKeyCredential]:
    """Read ``subject:secret`` entries.

    The comma-separated shape ``CORS_ORIGINS`` and ``LLM_PRICING`` already use,
    for the same reason: it survives an environment variable without JSON.
    Split once from the left, so a secret containing a colon stays intact.
    """
    parsed: list[ApiKeyCredential] = []
    for entry in entries:
        if not entry.strip():
            continue
        subject, separator, secret = entry.partition(":")
        if not separator or not subject.strip() or not secret.strip():
            raise AuthenticationConfigurationError(
                "A key entry must be 'subject:secret'.",
                # Never the entry itself -- it is half credential.
                details={"reason": "malformed_entry"},
            )
        if len(secret.strip()) < MIN_KEY_LENGTH:
            raise AuthenticationConfigurationError(
                f"An API key must be at least {MIN_KEY_LENGTH} characters.",
                details={"subject": subject.strip()},
            )
        parsed.append(
            ApiKeyCredential(subject=subject.strip(), secret=SecretStr(secret.strip()))
        )
    return parsed
