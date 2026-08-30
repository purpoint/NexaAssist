"""HTTP API layer, split by version.

Each version is a package (``v1``, and ``v2`` when it exists) exposing a
``router`` that ``app.main`` mounts under a version prefix.
"""
