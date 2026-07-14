"""Safe, operator-facing exception hierarchy for Wit."""


class WitError(Exception):
    """Base class for expected failures whose messages are safe to display."""
