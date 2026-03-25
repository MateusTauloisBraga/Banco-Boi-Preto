"""
Strava API client: token refresh, segment details, segment leaderboard.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "https://www.strava.com/api/v3"


def get_access_token() -> str | None:
    """Refresh and return a valid access token."""
    client_id = os.getenv("STRAVA_CLIENT_ID")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")
    if not all((client_id, client_secret, refresh_token)):
        return None
    r = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    if r.status_code != 200:
        return None
    return r.json().get("access_token")


def get_segment(segment_id: str | int, access_token: str) -> dict | None:
    """Fetch segment by ID. Returns segment object with map.polyline (and map.summary_polyline)."""
    r = requests.get(
        f"{BASE}/segments/{segment_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if r.status_code != 200:
        return None
    return r.json()


def get_segment_leaderboard(
    segment_id: str | int,
    access_token: str,
    per_page: int = 200,
    page: int = 1,
) -> tuple[dict | None, str | None]:
    """Fetch segment leaderboard. Returns (data dict or None, error_message or None)."""
    r = requests.get(
        f"{BASE}/segments/{segment_id}/leaderboard",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"per_page": per_page, "page": page},
        timeout=10,
    )
    if r.status_code != 200:
        try:
            err = r.json().get("message", r.text or f"HTTP {r.status_code}")
        except Exception:
            err = r.text or f"HTTP {r.status_code}"
        return None, err
    try:
        return r.json(), None
    except Exception as e:
        return None, str(e)


def get_leaderboard_entries(segment_id: str | int, access_token: str, max_pages: int = 5) -> tuple[list[dict], str | None]:
    """Fetch leaderboard entries across multiple pages. Returns (entries list, error_message or None)."""
    entries = []
    for page in range(1, max_pages + 1):
        data, err = get_segment_leaderboard(segment_id, access_token, per_page=200, page=page)
        if err:
            return entries, err  # Return what we have so far plus error
        if not data:
            break
        page_entries = data.get("entries") or []
        if not page_entries:
            break
        entries.extend(page_entries)
    return entries, None
