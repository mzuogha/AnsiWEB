"""Small helpers shared across the modules."""
import datetime as dt


def now() -> str:
    """The timestamp format used everywhere AnsiWEB records a time."""
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
