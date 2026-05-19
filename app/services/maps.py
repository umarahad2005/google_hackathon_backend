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

# Known Pakistani cities. If the user's location text already names one of
# these, we must NOT force an "Islamabad" context onto the geocoder — doing
# so is what made "Mansoorah Home Lahore" resolve to Blue Area, Islamabad.
PK_CITIES: tuple[str, ...] = (
    "lahore", "karachi", "islamabad", "rawalpindi", "faisalabad",
    "multan", "peshawar", "quetta", "sialkot", "gujranwala", "hyderabad",
    "bahawalpur", "sargodha", "abbottabad", "sukkur", "larkana", "mardan",
    "sahiwal", "okara", "wah", "dera ghazi khan", "mirpur", "muzaffarabad",
)

# Default city context for bare sector text (e.g. "G-13") — the gazetteer
# and primary service area are Islamabad/Rawalpindi.
DEFAULT_CITY_CONTEXT = "Islamabad, Pakistan"

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


def resolve_location(location_text: str) -> tuple[tuple[float, float], str] | None:
    """
    Resolve a location text to ((lat, lng), source).

    Real-Maps-first: Google Geocoding API is authoritative. The offline
    sector gazetteer is only a fallback when the API key is absent or the
    call fails. Returns None (honest failure) instead of silently
    defaulting to city center, so the caller can flag it as degraded.

    source ∈ {"geocoding", "gazetteer"}.
    """
    raw = location_text.strip()
    lowered = raw.lower()

    # Detect whether the user already named a city. If so, geocode the text
    # as-is (just country-scoped); only bare sector text falls back to the
    # Islamabad default context.
    mentioned_city = next((c for c in PK_CITIES if c in lowered), None)
    if mentioned_city:
        query = f"{raw}, Pakistan"
    else:
        query = f"{raw}, {DEFAULT_CITY_CONTEXT}"

    # For the offline gazetteer match we only strip the *default* city, not
    # an explicitly-requested one (a Lahore query must never match an
    # Islamabad sector key).
    normalized = lowered.replace(",", "")
    if not mentioned_city:
        normalized = normalized.replace("islamabad", "")
    normalized = normalized.strip()

    # 1. Real Geocoding API (authoritative)
    client = _get_maps_client()
    if client:
        try:
            cache_key = f"geocode:{query.lower()}"
            if cache_key in _cache:
                return _cache[cache_key], "geocoding"
            # region="PK" + country component keep results inside Pakistan
            # while still honouring the city the user actually asked for.
            results = client.geocode(
                query, region="pk", components={"country": "PK"}
            )
            if results:
                loc = results[0]["geometry"]["location"]
                coords = (loc["lat"], loc["lng"])
                _cache[cache_key] = coords
                logger.info(
                    f"Location '{location_text}' resolved via Geocoding API: {coords}"
                )
                return coords, "geocoding"
            logger.warning(f"Geocoding API returned no result for '{location_text}'")
        except Exception as e:
            logger.warning(f"Geocoding API failed for '{location_text}': {e}")

    # 2. Offline gazetteer fallback (degraded). The gazetteer is
    # Islamabad/Rawalpindi-only, so skip it entirely when the user asked
    # for another city — a wrong-city sector is worse than honest failure.
    gazetteer_ok = mentioned_city in (None, "islamabad", "rawalpindi")
    for key, coords in (SECTOR_GAZETTEER.items() if gazetteer_ok else ()):
        if key in normalized or normalized in key:
            logger.info(
                f"Location '{location_text}' resolved via gazetteer fallback: "
                f"{key} → {coords}"
            )
            return coords, "gazetteer"

    # 3. Honest failure — no silent city-center default.
    logger.error(f"Location '{location_text}' could not be resolved.")
    return None


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


def _distance_matrix_refine(
    origin: tuple[float, float],
    candidates: list[ProviderCandidate],
) -> tuple[bool, float | None]:
    """
    Replace straight-line distances with REAL driving distances from the
    Google Distance Matrix API for the given candidates (mutated in place).

    Returns (used_real_api, nearest_eta_min). Best-effort: on any failure
    the haversine distances are left untouched.
    """
    client = _get_maps_client()
    if not client or not candidates:
        return False, None
    try:
        dests = [(c.lat, c.lng) for c in candidates]
        cache_key = (
            f"dm:{origin[0]:.4f}:{origin[1]:.4f}:"
            f"{len(dests)}:{hash(tuple(dests))}"
        )
        if cache_key in _cache:
            matrix = _cache[cache_key]
        else:
            matrix = client.distance_matrix(
                origins=[origin],
                destinations=dests,
                mode="driving",
            )
            _cache[cache_key] = matrix

        elements = matrix.get("rows", [{}])[0].get("elements", [])
        nearest_eta_min: float | None = None
        for cand, el in zip(candidates, elements):
            if el.get("status") == "OK":
                cand.distance_km = round(
                    el["distance"]["value"] / 1000.0, 2
                )
                eta = el["duration"]["value"] / 60.0
                if nearest_eta_min is None or eta < nearest_eta_min:
                    nearest_eta_min = round(eta, 1)
        return True, nearest_eta_min
    except Exception as e:
        logger.warning(f"Distance Matrix API failed: {e}")
        return False, None


def _enrich_place_details(candidates: list[ProviderCandidate]) -> int:
    """
    Pull REAL name / phone / rating from the Google Place Details API for
    Places-sourced candidates (Nearby Search does not return phone numbers).
    Mutates in place, capped + cached to control cost. Returns count enriched.
    """
    client = _get_maps_client()
    if not client:
        return 0
    enriched = 0
    for c in candidates:
        if c.source != ProviderSource.PLACES or not c.provider_id:
            continue
        if c.phone:  # already have it
            continue
        cache_key = f"placedetails:{c.provider_id}"
        try:
            if cache_key in _cache:
                det = _cache[cache_key]
            else:
                det = client.place(
                    place_id=c.provider_id,
                    fields=[
                        "name",
                        "formatted_phone_number",
                        "international_phone_number",
                        "rating",
                        "user_ratings_total",
                    ],
                ).get("result", {})
                _cache[cache_key] = det
            if det.get("name"):
                c.name = det["name"]
            phone = (det.get("formatted_phone_number")
                     or det.get("international_phone_number"))
            if phone:
                c.phone = phone
            if det.get("rating") is not None:
                c.rating = float(det["rating"])
            enriched += 1
        except Exception as e:
            logger.warning(f"Place Details failed for {c.provider_id}: {e}")
    return enriched


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
    resolved = resolve_location(location_text)
    if resolved is None:
        return DiscoveryResult(
            candidates=[],
            radius_used_km=radius_km,
            reasoning=(
                f"Could not resolve location '{location_text}' via Google "
                f"Geocoding or the sector gazetteer. Discovery aborted."
            ),
            degraded=True,
        )

    (lat, lng), geo_source = resolved

    # Real Google Places + seeded PostGIS DB, merged.
    places_candidates = _places_nearby(category, lat, lng, radius_km)
    places_failed = not places_candidates
    db_candidates = await _db_providers_within(category, lat, lng, radius_km)

    merged = _deduplicate(places_candidates + db_candidates)
    merged.sort(key=lambda c: c.distance_km)
    merged = merged[:10]

    # Real driving distances/ETA via Distance Matrix API.
    dm_used, nearest_eta = _distance_matrix_refine((lat, lng), merged)
    if dm_used:
        merged.sort(key=lambda c: c.distance_km)

    # Real name/phone/rating for the Google-Places providers.
    enriched = _enrich_place_details(merged)

    places_count = sum(1 for c in merged if c.source == ProviderSource.PLACES)
    db_count = sum(1 for c in merged if c.source == ProviderSource.DB)
    reasoning = (
        f"Searched '{category}' within {radius_km}km of {location_text} "
        f"(→ {lat:.4f},{lng:.4f} via {geo_source}). "
        f"{len(merged)} candidates: {places_count} Google Places, "
        f"{db_count} seeded DB. "
        f"Distances: {'real driving (Distance Matrix)' if dm_used else 'straight-line haversine'}. "
        f"{enriched} enriched with Place Details (real phone/rating). "
    )
    if places_failed:
        reasoning += "Google Places returned nothing — DB-only (degraded). "
    if merged:
        reasoning += f"Nearest: {merged[0].name} at {merged[0].distance_km}km"
        reasoning += f" (~{nearest_eta} min drive)." if nearest_eta else "."
    else:
        reasoning += "No providers found in this radius."

    # Honest degraded signal: gazetteer fallback OR Places dead but DB saved it.
    degraded = geo_source == "gazetteer" or (places_failed and db_count > 0)

    return DiscoveryResult(
        candidates=merged,
        radius_used_km=radius_km,
        reasoning=reasoning,
        degraded=degraded,
    )
