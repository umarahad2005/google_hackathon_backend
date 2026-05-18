"""
Zimma AI — Google Maps Platform Client.

Provides geocoding, nearby search, distance matrix, and the unified
find_candidates() tool that merges Places API + Supabase PostGIS results.

Owner: Maps/Geo Engineer (06)
Source: agents/skills/google-maps-integration.md
         agents/agents/06-maps-geo-engineer.md
"""

from __future__ import annotations

import logging
from typing import Any

import googlemaps

from app.settings import get_settings
from app.models import ProviderCandidate, DiscoveryResult, ProviderSource, PriceBand
from app.services.supabase import find_providers_within, find_providers_by_category

logger = logging.getLogger(__name__)

# ======================================================================
# Sector Gazetteer — Offline Fallback (REQUIRED)
# Hardcoded lat/lng for Islamabad/Rawalpindi sectors.
# Source: agents/skills/google-maps-integration.md
# ======================================================================

SECTOR_GAZETTEER: dict[str, tuple[float, float]] = {
    # Islamabad sectors
    "g-10":       (33.6844, 72.9975),
    "g-10/1":     (33.6870, 73.0010),
    "g-10/2":     (33.6830, 72.9950),
    "g-10/3":     (33.6810, 72.9930),
    "g-10/4":     (33.6860, 72.9990),
    "g-11":       (33.6700, 72.9850),
    "g-11/1":     (33.6720, 72.9880),
    "g-11/2":     (33.6690, 72.9830),
    "g-11/3":     (33.6670, 72.9810),
    "g-11/4":     (33.6710, 72.9860),
    "g-12":       (33.6550, 72.9750),
    "g-13":       (33.6350, 72.9640),
    "g-13/1":     (33.6380, 72.9670),
    "g-13/2":     (33.6330, 72.9620),
    "g-13/3":     (33.6310, 72.9600),
    "g-13/4":     (33.6370, 72.9660),
    "g-14":       (33.6200, 72.9500),
    "g-15":       (33.6050, 72.9400),
    "f-5":        (33.7220, 73.0450),
    "f-6":        (33.7180, 73.0300),
    "f-7":        (33.7130, 73.0150),
    "f-8":        (33.7050, 73.0050),
    "f-8/1":      (33.7080, 73.0080),
    "f-8/2":      (33.7030, 73.0020),
    "f-8/3":      (33.7010, 72.9990),
    "f-8/4":      (33.7060, 73.0060),
    "f-9":        (33.6950, 72.9950),
    "f-10":       (33.6900, 72.9850),
    "f-10/1":     (33.6930, 72.9880),
    "f-10/2":     (33.6880, 72.9830),
    "f-10/3":     (33.6870, 72.9810),
    "f-10/4":     (33.6920, 72.9860),
    "f-11":       (33.6800, 72.9750),
    "i-8":        (33.6650, 73.0700),
    "i-8/1":      (33.6680, 73.0730),
    "i-8/2":      (33.6630, 73.0680),
    "i-8/3":      (33.6620, 73.0660),
    "i-8/4":      (33.6660, 73.0710),
    "i-9":        (33.6550, 73.0600),
    "i-10":       (33.6450, 73.0500),
    "i-11":       (33.6350, 73.0400),
    "e-7":        (33.7350, 73.0600),
    "e-11":       (33.6800, 73.0400),
    "h-8":        (33.6900, 73.0550),
    "h-9":        (33.6750, 73.0450),
    "blue area":  (33.7100, 73.0580),
    # Rawalpindi sectors
    "saddar":     (33.5990, 73.0500),
    "satellite town": (33.6150, 73.0650),
    "bahria town":(33.5200, 73.0900),
    "dha":        (33.5150, 73.1100),
    "chaklala":   (33.6050, 73.0750),
    "westridge":  (33.6100, 73.0400),
    "adiala road":(33.5700, 73.0200),
    "pwd":        (33.5600, 73.0800),
}

# Category mapping for Places API search terms
CATEGORY_SEARCH_TERMS: dict[str, str] = {
    "ac_technician": "AC repair technician air conditioning",
    "electrician": "electrician electrical services",
    "plumber": "plumber plumbing services",
    "tutor": "home tutor tuition teacher",
    "beautician": "beauty parlor beautician salon",
    "carpenter": "carpenter woodwork furniture repair",
    "appliance_repair": "appliance repair washing machine fridge",
}

# Response cache for deterministic demos
_cache: dict[str, Any] = {}


def _get_maps_client() -> googlemaps.Client | None:
    """Get Maps client, or None if key not available."""
    try:
        s = get_settings()
        if s.google_maps_api_key and s.google_maps_api_key != "your-maps-api-key-here":
            return googlemaps.Client(key=s.google_maps_api_key)
    except Exception as e:
        logger.warning(f"Maps client init failed: {e}")
    return None


def resolve_location(location_text: str) -> tuple[float, float] | None:
    """
    Resolve a location text to (lat, lng).
    Strategy: Geocoding API → sector gazetteer fallback.
    """
    normalized = location_text.strip().lower().replace(",", "").replace("islamabad", "").strip()

    # Check gazetteer first (fast + offline)
    for key, coords in SECTOR_GAZETTEER.items():
        if key in normalized or normalized in key:
            logger.info(f"Location '{location_text}' resolved via gazetteer: {key} → {coords}")
            return coords

    # Try Geocoding API
    client = _get_maps_client()
    if client:
        try:
            cache_key = f"geocode:{normalized}"
            if cache_key in _cache:
                return _cache[cache_key]

            results = client.geocode(f"{location_text}, Islamabad, Pakistan")
            if results:
                loc = results[0]["geometry"]["location"]
                coords = (loc["lat"], loc["lng"])
                _cache[cache_key] = coords
                logger.info(f"Location '{location_text}' resolved via Geocoding API: {coords}")
                return coords
        except Exception as e:
            logger.warning(f"Geocoding API failed for '{location_text}': {e}")

    # Fallback: default to Islamabad center
    logger.warning(f"Location '{location_text}' unresolved, defaulting to Islamabad center")
    return (33.6844, 73.0479)


def _places_nearby(
    category: str,
    lat: float,
    lng: float,
    radius_km: float,
) -> list[ProviderCandidate]:
    """Search Google Places API (New) for providers."""
    client = _get_maps_client()
    if not client:
        return []

    search_term = CATEGORY_SEARCH_TERMS.get(category, category)
    cache_key = f"places:{category}:{lat:.4f}:{lng:.4f}:{radius_km}"

    if cache_key in _cache:
        return _cache[cache_key]

    try:
        results = client.places_nearby(
            location=(lat, lng),
            radius=int(radius_km * 1000),
            keyword=search_term,
        )

        candidates = []
        for place in results.get("results", [])[:10]:
            ploc = place["geometry"]["location"]
            # Calculate approximate distance
            from math import radians, sin, cos, sqrt, atan2
            R = 6371  # Earth radius km
            dlat = radians(ploc["lat"] - lat)
            dlng = radians(ploc["lng"] - lng)
            a = sin(dlat/2)**2 + cos(radians(lat)) * cos(radians(ploc["lat"])) * sin(dlng/2)**2
            dist = R * 2 * atan2(sqrt(a), sqrt(1-a))

            candidates.append(ProviderCandidate(
                provider_id=place.get("place_id", ""),
                name=place.get("name", "Unknown"),
                category=category,
                lat=ploc["lat"],
                lng=ploc["lng"],
                distance_km=round(dist, 2),
                rating=place.get("rating"),
                price_band=None,
                open_now=place.get("opening_hours", {}).get("open_now"),
                languages=["ur", "en"],
                source=ProviderSource.PLACES,
            ))

        _cache[cache_key] = candidates
        return candidates

    except Exception as e:
        logger.warning(f"Places API failed: {e}")
        return []


async def _db_providers_within(
    category: str,
    lat: float,
    lng: float,
    radius_km: float,
) -> list[ProviderCandidate]:
    """Query Supabase providers via PostGIS."""
    try:
        rows = await find_providers_within(
            category=category,
            lat=lat,
            lng=lng,
            radius_m=radius_km * 1000,
        )
        candidates = []
        for row in rows:
            candidates.append(ProviderCandidate(
                provider_id=row["id"],
                name=row["name"],
                category=row["category"],
                lat=row.get("lat", lat),
                lng=row.get("lng", lng),
                distance_km=round(row.get("distance_km", 0), 2),
                rating=float(row["rating"]) if row.get("rating") else None,
                price_band=row.get("price_band"),
                open_now=None,
                languages=row.get("languages", []),
                source=ProviderSource.DB,
                working_hours=row.get("working_hours"),
                phone=row.get("phone"),
            ))
        return candidates
    except Exception as e:
        logger.warning(f"DB provider query failed: {e}")
        return []


def _deduplicate(
    candidates: list[ProviderCandidate],
) -> list[ProviderCandidate]:
    """De-duplicate by name similarity + geo proximity (< 200m)."""
    seen: dict[str, ProviderCandidate] = {}

    for c in candidates:
        name_key = c.name.lower().strip()
        is_dup = False
        for existing_key, existing in seen.items():
            # Name similarity check
            if name_key == existing_key or name_key in existing_key or existing_key in name_key:
                # Geo proximity check (< 0.2 km)
                from math import radians, sin, cos, sqrt, atan2
                R = 6371
                dlat = radians(c.lat - existing.lat)
                dlng = radians(c.lng - existing.lng)
                a = sin(dlat/2)**2 + cos(radians(c.lat)) * cos(radians(existing.lat)) * sin(dlng/2)**2
                dist = R * 2 * atan2(sqrt(a), sqrt(1-a))
                if dist < 0.2:
                    is_dup = True
                    # Prefer DB source (has more metadata)
                    if c.source == ProviderSource.DB:
                        seen[existing_key] = c
                    break
        if not is_dup:
            seen[name_key] = c

    return list(seen.values())


async def find_candidates(
    category: str,
    location_text: str,
    radius_km: float,
) -> DiscoveryResult:
    """
    The ONE tool the agents call.
    Merges real Places results + seeded Supabase providers (PostGIS),
    de-duplicates, and returns distance-sorted candidates.

    Source: agents/skills/google-maps-integration.md
    """
    coords = resolve_location(location_text)
    if not coords:
        return DiscoveryResult(
            candidates=[],
            radius_used_km=radius_km,
            reasoning=f"Could not resolve location '{location_text}'",
            degraded=True,
        )

    lat, lng = coords
    places_failed = False

    # Fetch from both sources
    places_candidates = _places_nearby(category, lat, lng, radius_km)
    if not places_candidates:
        places_failed = True

    db_candidates = await _db_providers_within(category, lat, lng, radius_km)

    # Merge + deduplicate
    all_candidates = places_candidates + db_candidates
    merged = _deduplicate(all_candidates)

    # Sort by distance
    merged.sort(key=lambda c: c.distance_km)

    # Keep top ~10
    merged = merged[:10]

    # Build reasoning
    places_count = sum(1 for c in merged if c.source == ProviderSource.PLACES)
    db_count = sum(1 for c in merged if c.source == ProviderSource.DB)
    reasoning = (
        f"Searched for '{category}' within {radius_km}km of {location_text} "
        f"(resolved to {lat:.4f}, {lng:.4f}). "
        f"Found {len(merged)} candidates: {places_count} from Google Places, "
        f"{db_count} from seeded database. "
    )
    if places_failed:
        reasoning += "Google Places API unavailable — using database only (degraded mode). "
    if merged:
        reasoning += f"Nearest: {merged[0].name} at {merged[0].distance_km}km."
    else:
        reasoning += "No providers found in this radius."

    return DiscoveryResult(
        candidates=merged,
        radius_used_km=radius_km,
        reasoning=reasoning,
        degraded=places_failed and db_count > 0,
    )
