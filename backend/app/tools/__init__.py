"""The tool system.

``base`` defines what a tool is, ``registry`` holds the ones available, and
``execution`` runs them. Nothing here knows about agents: M6 builds the
mechanism, and the loop that decides which tool to call belongs to M7.
"""
