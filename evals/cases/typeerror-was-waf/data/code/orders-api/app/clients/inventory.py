"""Synthetic snapshot of the unchecked JSON assumption."""


def _safe_json(response):
    if "application/json" not in response.headers.get("content-type", ""):
        return None
    try:
        return response.json()
    except ValueError:
        return None


def inventory_items(response):
    return _safe_json(response)["items"]
