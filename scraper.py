"""
Oahu snorkeling spots scraper.

Primary source: OpenStreetMap Overpass API (free, open, no auth required).
Queries for snorkeling/diving nodes and nearby reef/beach features within
Oahu's bounding box, then enriches each spot with a Wikipedia summary if
one exists.

Output: snorkeling_spots.json
"""

import json
import time
import requests

# ── constants ──────────────────────────────────────────────────────────────────

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
WIKI_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"

HEADERS = {
    "User-Agent": (
        "OahuSnorkelScraper/1.0 "
        "(+https://github.com/matthewjarrell-commits/oahu-snorkeling-)"
    )
}

# Bounding box: [south, west, north, east]  (covers the island of Oahu)
OAHU_BBOX = "21.25,-158.30,21.75,-157.60"

OVERPASS_QUERY = f"""
[out:json][timeout:30];
(
  node["sport"="snorkeling"]({OAHU_BBOX});
  node["sport"="diving"]({OAHU_BBOX});
  node["leisure"="beach"]["sport"="snorkeling"]({OAHU_BBOX});
  node["natural"="reef"]({OAHU_BBOX});
  node["natural"="bay"]["name"]({OAHU_BBOX});
  node["tourism"="attraction"]["name"]({OAHU_BBOX});
  way["natural"="beach"]["name"]({OAHU_BBOX});
  way["natural"="reef"]["name"]({OAHU_BBOX});
  way["sport"="snorkeling"]({OAHU_BBOX});
  way["sport"="diving"]({OAHU_BBOX});
);
out center;
"""

# Curated fallback list — used when the Overpass API returns fewer than 5 named spots
CURATED_SPOTS: list[dict] = [
    {
        "name": "Hanauma Bay",
        "lat": 21.2692,
        "lng": -157.6942,
        "description": "Hawaii's most popular snorkeling destination, a protected marine life conservation district inside a volcanic crater bay.",
        "difficulty": "Beginner",
        "marine_life": ["sea turtle", "reef fish", "parrotfish", "coral"],
        "best_season": "Year-round (closed Tuesdays)",
        "source": "curated",
    },
    {
        "name": "Shark's Cove",
        "lat": 21.6456,
        "lng": -158.0681,
        "description": "A lava-rock enclosed cove on the North Shore, excellent for snorkeling in summer when seas are calm. Named for its shark-fin-shaped rocks.",
        "difficulty": "Intermediate",
        "marine_life": ["sea turtle", "octopus", "eel", "reef fish", "coral"],
        "best_season": "May–September",
        "source": "curated",
    },
    {
        "name": "Electric Beach (Kahe Point)",
        "lat": 21.3722,
        "lng": -158.1261,
        "description": "Warm water discharged by a nearby power plant attracts spinner dolphins, sea turtles, and a wide variety of marine life.",
        "difficulty": "Intermediate",
        "marine_life": ["spinner dolphin", "sea turtle", "reef fish", "eagle ray"],
        "best_season": "Year-round",
        "source": "curated",
    },
    {
        "name": "Lanikai Beach",
        "lat": 21.3892,
        "lng": -157.7175,
        "description": "Calm, crystal-clear water with a sandy bottom ideal for beginners. Fronts the offshore Mokulua Islands.",
        "difficulty": "Beginner",
        "marine_life": ["reef fish", "sea turtle", "coral"],
        "best_season": "Year-round",
        "source": "curated",
    },
    {
        "name": "Turtle Canyon",
        "lat": 21.2808,
        "lng": -157.8378,
        "description": "Offshore reef south of Waikiki reachable by boat tour. Famous for large resident populations of Hawaiian green sea turtles.",
        "difficulty": "Beginner",
        "marine_life": ["sea turtle", "reef fish", "coral", "Hawaiian monk seal"],
        "best_season": "Year-round",
        "source": "curated",
    },
    {
        "name": "Waimea Bay",
        "lat": 21.6428,
        "lng": -158.0658,
        "description": "Calm and perfect for snorkeling in summer; a famous big-wave surf spot in winter. Good visibility over rocky reef near the shore.",
        "difficulty": "Beginner",
        "marine_life": ["sea turtle", "reef fish", "parrotfish"],
        "best_season": "May–September",
        "source": "curated",
    },
    {
        "name": "Ko Olina Lagoons",
        "lat": 21.3358,
        "lng": -158.1225,
        "description": "Four man-made lagoons on the west side with calm, protected water. Great for families and beginners; modest reef around the rock walls.",
        "difficulty": "Beginner",
        "marine_life": ["reef fish", "sea turtle"],
        "best_season": "Year-round",
        "source": "curated",
    },
    {
        "name": "Three Tables",
        "lat": 21.6469,
        "lng": -158.0700,
        "description": "Named for three flat reef platforms visible above the surface. Excellent snorkeling over lava formations next to Shark's Cove.",
        "difficulty": "Beginner",
        "marine_life": ["reef fish", "eel", "coral", "octopus"],
        "best_season": "May–September",
        "source": "curated",
    },
    {
        "name": "Kailua Beach Park",
        "lat": 21.3925,
        "lng": -157.7258,
        "description": "Long sandy beach with calm, clear water. The outer reef near Flat Island offers good snorkeling for paddlers.",
        "difficulty": "Beginner",
        "marine_life": ["sea turtle", "reef fish"],
        "best_season": "Year-round",
        "source": "curated",
    },
    {
        "name": "Pupukea Beach Park (Old Quarry)",
        "lat": 21.6475,
        "lng": -158.0648,
        "description": "A sheltered tidal pool area adjacent to Shark's Cove and Three Tables, safe for children when seas are calm.",
        "difficulty": "Beginner",
        "marine_life": ["reef fish", "sea urchin", "crab"],
        "best_season": "May–September",
        "source": "curated",
    },
]


# ── helpers ────────────────────────────────────────────────────────────────────

def fetch_overpass() -> list[dict]:
    print("Querying OpenStreetMap Overpass API…")
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": OVERPASS_QUERY},
            headers=HEADERS,
            timeout=45,
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
        print(f"  Overpass returned {len(elements)} elements.")
        return elements
    except requests.RequestException as e:
        print(f"  [error] Overpass API: {e}")
        return []


def element_to_spot(el: dict) -> dict | None:
    tags = el.get("tags", {})
    name = tags.get("name") or tags.get("name:en")
    if not name:
        return None

    # Coordinates: nodes have lat/lon directly; ways have a centre object
    if el["type"] == "node":
        lat, lng = el.get("lat"), el.get("lon")
    else:
        center = el.get("center", {})
        lat, lng = center.get("lat"), center.get("lon")

    spot: dict = {"name": name, "source": "openstreetmap"}
    if lat is not None:
        spot["lat"] = lat
    if lng is not None:
        spot["lng"] = lng

    for key in ("description", "description:en", "note"):
        if tags.get(key):
            spot["description"] = tags[key]
            break

    if tags.get("sport"):
        spot["sport"] = tags["sport"]
    if tags.get("natural"):
        spot["feature_type"] = tags["natural"]
    if tags.get("leisure"):
        spot["feature_type"] = tags.get("feature_type", tags["leisure"])
    if tags.get("website") or tags.get("url"):
        spot["website"] = tags.get("website") or tags.get("url")

    return spot


def enrich_with_wikipedia(spot: dict) -> dict:
    """Add a Wikipedia extract to the spot if a matching article exists."""
    query = spot["name"].replace(" ", "_")
    try:
        resp = requests.get(
            WIKI_API + query,
            headers={**HEADERS, "Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("type") == "standard" and data.get("extract"):
                spot.setdefault("description", data["extract"])
                spot["wikipedia_url"] = data.get("content_urls", {}).get("desktop", {}).get("page")
    except requests.RequestException:
        pass
    return spot


# ── main ───────────────────────────────────────────────────────────────────────

def scrape() -> list[dict]:
    elements = fetch_overpass()

    # Deduplicate by name
    seen: set[str] = set()
    osm_spots: list[dict] = []
    for el in elements:
        spot = element_to_spot(el)
        if spot and spot["name"] not in seen:
            seen.add(spot["name"])
            osm_spots.append(spot)

    print(f"  {len(osm_spots)} named spots from OpenStreetMap.")

    # Use curated list if OSM data is sparse
    if len(osm_spots) < 5:
        print("  Falling back to curated spot list.")
        spots = CURATED_SPOTS[:]
        # Merge in any OSM spots not already in curated list
        curated_names = {s["name"].lower() for s in spots}
        for s in osm_spots:
            if s["name"].lower() not in curated_names:
                spots.append(s)
    else:
        spots = osm_spots

    # Enrich with Wikipedia summaries (rate-limited)
    print("Enriching spots with Wikipedia summaries…")
    for i, spot in enumerate(spots):
        if "description" not in spot:
            print(f"  [{i+1}/{len(spots)}] {spot['name']}")
            enrich_with_wikipedia(spot)
            time.sleep(0.5)

    return spots


def main() -> None:
    spots = scrape()

    output = {
        "description": "Oahu snorkeling spots — compiled from OpenStreetMap and curated sources",
        "count": len(spots),
        "spots": spots,
    }

    output_path = "snorkeling_spots.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Saved {len(spots)} spots to {output_path}")


if __name__ == "__main__":
    main()
