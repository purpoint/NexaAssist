"""Human-in-the-loop.

``criteria`` decides when a request needs a person; ``queue`` records the ones
that do so a human can pick them up. The decision is deterministic and made
outside the model, for the same reason policy is: whether a customer reaches a
human should not vary run to run.
"""
