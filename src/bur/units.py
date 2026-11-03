"""Format sizes and ages for people to read"""

from __future__ import annotations

from datetime import timedelta

_BYTES_PER_KB = 1024
_BYTES_PER_MB = 1024**2
_BYTES_PER_GB = 1024**3


def readable_size(size: int) -> str:
    """Format a size in bytes as KB, MB or GB, whichever reads best

    :param size: Size in bytes
    :returns: The size with one decimal place and its unit, e.g. ``48.2 KB``
    """
    if size < _BYTES_PER_MB:
        return f"{size / _BYTES_PER_KB:.1f} KB"
    if size < _BYTES_PER_GB:
        return f"{size / _BYTES_PER_MB:.1f} MB"
    return f"{size / _BYTES_PER_GB:.1f} GB"


def readable_age(age: timedelta) -> str:
    """Format an age as days and hours, or hours and minutes under a day

    :param age: How long ago something happened
    :returns: The age, e.g. ``2d 4h`` or ``3h 12m``
    """
    total_minutes = max(int(age.total_seconds() // 60), 0)
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m"
