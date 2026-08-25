"""The five cases, cheapest first.

The order is the order they run in and it is not arbitrary: a run that is going to fail
because the provider is unreachable, the model is aliased or the skill never opens should
discover that on the cheapest case rather than after the merge session has been paid for.
"""
