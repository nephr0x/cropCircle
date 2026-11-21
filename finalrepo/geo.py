"""Turn an address into coordinates. OpenStreetMap first, built-in town list as a fallback."""
import math

import httpx

import config


def km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def locate(address="", city="", county="", state="", zip_code=""):
    """Returns (lat, lon, how). Never raises."""
    parts = [p for p in (address, city, f"{county} County" if county else "", state, zip_code) if p]
    if parts:
        try:
            r = httpx.get("https://nominatim.openstreetmap.org/search",
                          params={"q": ", ".join(parts), "format": "jsonv2", "limit": 1},
                          headers={"User-Agent": "FarmLine-hackathon-demo"}, timeout=5)
            hit = r.json()
            if hit:
                return float(hit[0]["lat"]), float(hit[0]["lon"]), "address"
        except Exception:
            pass
    for name, (lat, lon) in config.PLACES.items():
        if city and name.lower() == city.strip().lower():
            return lat, lon, "town"
    lat, lon = config.PLACES[config.DEFAULT_PLACE]
    return lat, lon, "default"
