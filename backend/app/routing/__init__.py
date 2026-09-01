"""Intent routing.

``handlers`` defines what a handler is, ``registry`` maps intents to them, and
``router`` decides which one runs -- including what to do when the
classification is unusable. Routing chooses a handler; it does not implement
one.
"""
