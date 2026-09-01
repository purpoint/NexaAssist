"""The deterministic policy engine.

``rules`` defines what a rule is and how a set of them is evaluated;
``enforcement`` applies the result to a proposed reply. Nothing here consults a
model: that is the point. Policy is the part of the system whose behaviour must
be predictable, auditable, and identical on every run.
"""
