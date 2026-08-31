"""Merge competitor location files and render a Folium map."""

from __future__ import annotations

import json
from pathlib import Path

import folium
from folium.plugins import MarkerCluster

COMPETITOR_COLORS = {
    "NON": "#1f9d57",
    "Villbrygg": "#c45c26",
}

DATA_DIR = Path(__file__).resolve().parent / "data"
OUT_HTML = DATA_DIR / "competitors_map.html"


def load_locations() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(DATA_DIR.glob("*_locations.json")):
        with path.open(encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, list):
            rows.extend(payload)
    return rows


def build_map(rows: list[dict]) -> folium.Map:
    plottable = [
        r for r in rows
        if r.get("latitude") is not None and r.get("longitude") is not None
    ]
    if not plottable:
        return folium.Map(location=[20, 0], zoom_start=2, tiles="OpenStreetMap")

    avg_lat = sum(r["latitude"] for r in plottable) / len(plottable)
    avg_lng = sum(r["longitude"] for r in plottable) / len(plottable)
    # OpenStreetMap tiles — no API key (Carto/Mapbox often show a grey key overlay)
    m = folium.Map(location=[avg_lat, avg_lng], zoom_start=3, tiles="OpenStreetMap")

    clusters: dict[str, MarkerCluster] = {}
    for competitor in sorted({r.get("competitor", "Unknown") for r in plottable}):
        clusters[competitor] = MarkerCluster(name=competitor).add_to(m)

    for row in rows:
        lat, lng = row.get("latitude"), row.get("longitude")
        if lat is None or lng is None:
            continue
        competitor = row.get("competitor") or "Unknown"
        color = COMPETITOR_COLORS.get(competitor, "#3388ff")
        popup = (
            f"<b>{row.get('name', '')}</b><br>"
            f"Competitor: {competitor}<br>"
            f"Type: {row.get('venue_type', '')}<br>"
            f"Address: {row.get('address', '')}"
        )
        folium.CircleMarker(
            location=[lat, lng],
            radius=4,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            popup=folium.Popup(popup, max_width=280),
            tooltip=f"{competitor}: {row.get('name', '')}",
        ).add_to(clusters[competitor])

    folium.LayerControl(collapsed=False).add_to(m)
    return m


def main() -> None:
    rows = load_locations()
    if not rows:
        raise SystemExit(f"No *_locations.json files found in {DATA_DIR}. Run a scraper first.")
    m = build_map(rows)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    m.save(str(OUT_HTML))
    by_comp: dict[str, int] = {}
    for row in rows:
        c = row.get("competitor") or "?"
        by_comp[c] = by_comp.get(c, 0) + 1
    print(f"Mapped {len(rows)} locations -> {OUT_HTML}")
    print("By competitor:", by_comp)


if __name__ == "__main__":
    main()
