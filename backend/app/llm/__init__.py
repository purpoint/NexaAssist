"""The language-model layer.

``base`` defines the vendor-neutral contract, ``providers`` implements it, and
``factory`` chooses between implementations. Callers depend on ``base`` only --
nothing outside this package should import a provider module directly.
"""
