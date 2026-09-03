# NexaAssist backend image.
#
# Built from the repository root so the build context is the backend directory
# only:
#
#     docker build -f backend/Dockerfile -t nexaassist-backend backend
#
# Two stages. The builder installs into a virtualenv and the runtime copies
# that virtualenv across, so the compiler toolchain some wheels need is present
# where packages are built and absent from the image that runs in production.
#
# Nothing here runs a migration. Alembic owns the schema, and applying one is
# an explicit operator action against a database somebody chose -- a container
# that migrates on boot decides that for them, and does it once per replica.

# --- builder -----------------------------------------------------------------

FROM python:3.12-slim-bookworm AS builder

# Dependencies come from the lock file, not the range list: two builds a month
# apart should install the same bytes. requirements.txt still states what the
# project accepts; the lock states what this image is made of.
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# --- runtime -----------------------------------------------------------------

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH="/opt/venv/bin:$PATH"

# A dedicated account with no login shell and no home to write to. The process
# needs to read its code and open sockets; it never needs to modify itself, and
# a container that can rewrite its own application is a container an attacker
# can persist in.
RUN useradd --system --create-home --shell /usr/sbin/nologin --uid 10001 nexa

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Copied by name, never `COPY . .`. The repository root holds a git-ignored
# .env with a real provider key, and a wildcard copy is how that reaches an
# image somebody later pushes to a registry. Only these paths exist in the
# build context, and only these are taken from it.
COPY --chown=nexa:nexa app ./app
COPY --chown=nexa:nexa alembic ./alembic
COPY --chown=nexa:nexa alembic.ini ./alembic.ini

USER nexa

EXPOSE 8000

# No --reload: it watches the filesystem and restarts on change, which is a
# development convenience and a production liability. Host and port stay
# fixed because the container's own port is not the deployment's concern --
# a publish mapping decides that.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
