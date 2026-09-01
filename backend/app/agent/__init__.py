"""The agent core.

``state`` records what an agent did and enforces its budget; ``loop`` drives
the decide/act/observe cycle over the M6 tool system. Nothing here decides
*which* agent to run for a given request -- that is routing, and belongs to M8.
"""
