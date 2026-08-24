"""
Lightweight geo utilities. Deliberately dependency-free (no Google Maps /
Mapbox required) so the platform works fully without any paid API key.
If a maps provider key is added later (geocoding, live routing, ETAs),
these functions stay as the offline fallback — nothing else needs to change.
"""
import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lng points, in kilometers."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
