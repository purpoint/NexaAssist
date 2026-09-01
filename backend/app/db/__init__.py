"""Database access layer.

``base`` defines the declarative foundation every model inherits, ``engine``
owns the connection pool and its lifecycle, ``session`` hands request-scoped
sessions to callers, and ``errors`` maps failures onto the application's error
envelope.

Nothing here knows about the business domain, and no module outside this
package constructs an engine or a session directly.
"""
