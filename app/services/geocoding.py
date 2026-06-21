import hashlib
from abc import ABC, abstractmethod


class GeocodingProvider(ABC):
    """Port for converting a shipping address into coordinates.

    In production this would be an adapter around a real geocoding API
    (Google Maps, Mapbox, etc): an HTTP call with its own retry/timeout/
    error-handling policy. Keeping it behind this interface means swapping
    the mock for a real provider later is a one-file change -- nothing in
    the order service needs to know which implementation is in use.
    """

    @abstractmethod
    def geocode(self, address: dict) -> tuple[float, float]:
        """Return (latitude, longitude) for the given address dict."""
        raise NotImplementedError


# A handful of major-city coordinates so demo/seed addresses resolve to
# realistic, recognizable points instead of always hitting the hash fallback.
# Also imported by scripts/generate_seed_dump.py so warehouse locations in
# the seed data line up with cities this mock actually recognizes.
KNOWN_CITIES = {
    "new york": (40.7128, -74.0060),
    "brooklyn": (40.6782, -73.9442),
    "los angeles": (34.0522, -118.2437),
    "chicago": (41.8781, -87.6298),
    "houston": (29.7604, -95.3698),
    "phoenix": (33.4484, -112.0740),
    "philadelphia": (39.9526, -75.1652),
    "san antonio": (29.4241, -98.4936),
    "san diego": (32.7157, -117.1611),
    "dallas": (32.7767, -96.7970),
    "austin": (30.2672, -97.7431),
    "seattle": (47.6062, -122.3321),
    "denver": (39.7392, -104.9903),
    "boston": (42.3601, -71.0589),
    "miami": (25.7617, -80.1918),
    "atlanta": (33.7490, -84.3880),
    "san francisco": (37.7749, -122.4194),
    "portland": (45.5152, -122.6784),
    "las vegas": (36.1699, -115.1398),
    "detroit": (42.3314, -83.0458),
    "minneapolis": (44.9778, -93.2650),
    "newark": (40.7357, -74.1724),
    "columbus": (39.9612, -82.9988),
    "reno": (39.5296, -119.8138),
}


class MockGeocodingProvider(GeocodingProvider):
    """Deterministic stand-in for a real geocoding API.

    Looks up the city in a small known-cities table and adds a tiny,
    deterministic jitter derived from the street line so distinct addresses
    in the same city don't all collapse onto one point. Addresses in
    cities we don't recognize fall back to a deterministic hash-based
    coordinate -- not geographically meaningful, but stable (same address
    always geocodes to the same point), which is what the warehouse
    selection logic actually depends on.
    """

    def geocode(self, address: dict) -> tuple[float, float]:
        city = (address.get("city") or "").strip().lower()
        if city in KNOWN_CITIES:
            base_lat, base_lon = KNOWN_CITIES[city]
            jitter_lat, jitter_lon = self._jitter(address.get("line1", ""))
            return base_lat + jitter_lat, base_lon + jitter_lon
        return self._hash_to_coords(self._full_address_string(address))

    @staticmethod
    def _full_address_string(address: dict) -> str:
        parts = [
            address.get(k, "")
            for k in ("line1", "line2", "city", "state", "postal_code", "country")
        ]
        return ", ".join(p for p in parts if p)

    @staticmethod
    def _jitter(seed: str) -> tuple[float, float]:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        # +/- 0.1 degrees (~11km at the equator) - enough spread to
        # differentiate addresses within a city without leaving it.
        lat_j = (int(digest[:8], 16) / 0xFFFFFFFF - 0.5) * 0.2
        lon_j = (int(digest[8:16], 16) / 0xFFFFFFFF - 0.5) * 0.2
        return lat_j, lon_j

    @staticmethod
    def _hash_to_coords(seed: str) -> tuple[float, float]:
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        lat = (int(digest[:8], 16) / 0xFFFFFFFF) * 180 - 90
        lon = (int(digest[8:16], 16) / 0xFFFFFFFF) * 360 - 180
        return lat, lon
