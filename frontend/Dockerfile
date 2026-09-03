# NexaAssist web client image.
#
#     docker build -f frontend/Dockerfile -t nexaassist-frontend frontend
#
# Two stages, and the split matters more here than usual: node and the whole
# dependency tree are build-time only. What ships is static files and a server
# that hands them out.
#
# Vite inlines VITE_ variables at build time, so the backend URL is chosen when
# the image is built, not when it starts. That is a property of Vite rather
# than a decision made here; a deployment pointing at a different backend
# needs a different build, and the build argument below is where it says so.

# --- builder -----------------------------------------------------------------

FROM node:22-alpine AS builder

WORKDIR /build

# npm ci, not install: it installs exactly the lock file and fails if
# package.json and the lock have drifted, which is the behaviour an image
# wants. Copied before the sources so a change to a component does not
# reinstall the dependency tree.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json tsconfig.node.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src

# Baked in, so it is a build argument rather than an environment variable. The
# default points at the published port of the backend in compose, because the
# browser resolves this, not the container: `backend:8000` is reachable from
# other services and meaningless in a browser tab.
ARG VITE_API_BASE_URL=http://127.0.0.1:18000/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# `npm run build` typechecks first: an image that compiled a type error is an
# image nobody wanted.
RUN npm run build

# --- runtime -----------------------------------------------------------------

# The unprivileged image, which runs as uid 101 and listens on 8080 without
# needing to start as root to bind a low port.
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

# A single-page app: the router owns the paths, so anything that is not a file
# on disk has to come back as index.html rather than a 404.
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=builder --chown=nginx:nginx /build/dist /usr/share/nginx/html

EXPOSE 8080
