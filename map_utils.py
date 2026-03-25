"""
Map utilities: decode Strava polyline, interpolate position along path for animation.
"""
import polyline


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Decode Strava/Google encoded polyline to list of (lat, lng) tuples."""
    if not encoded:
        return []
    try:
        # polyline library uses (lat, lng) order
        return polyline.decode(encoded)
    except Exception:
        return []


def get_segment_path(segment: dict) -> list[tuple[float, float]]:
    """Extract decoded path from segment (prefer summary_polyline for fewer points)."""
    m = segment.get("map") or {}
    enc = m.get("summary_polyline") or m.get("polyline")
    return decode_polyline(enc or "")


def interpolate_position(path: list[tuple[float, float]], progress: float) -> tuple[float, float] | None:
    """
    Get (lat, lng) at progress along path. progress in [0, 1].
    progress=0 -> first point, progress=1 -> last point.
    """
    if not path or progress <= 0:
        return path[0] if path else None
    if progress >= 1:
        return path[-1]
    n = len(path) - 1
    if n == 0:
        return path[0]
    seg = progress * n
    i = int(seg)
    frac = seg - i
    lat = path[i][0] + frac * (path[i + 1][0] - path[i][0])
    lng = path[i][1] + frac * (path[i + 1][1] - path[i][1])
    return (lat, lng)


def athlete_progress_at_time(elapsed_time: float, athlete_elapsed_time: float) -> float:
    """
    Progress (0 to 1) along segment for an athlete at playback time elapsed_time.
    Assumes athlete runs at constant speed (position proportional to time).
    """
    if athlete_elapsed_time <= 0:
        return 1.0
    return min(1.0, elapsed_time / athlete_elapsed_time)
