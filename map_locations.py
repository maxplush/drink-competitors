"""Merge competitor location files and render a clean Folium map."""

from __future__ import annotations

import json
import re
from pathlib import Path

import folium
from branca.element import Element, MacroElement, Template
from folium.plugins import MarkerCluster

from scrapers.contact_fields import row_phone_email

COMPETITOR_COLORS = {
    "NON": "#136f63",
    "Villbrygg": "#8a6b16",
    "Unified Ferments": "#8b3a62",
    "Researched Prospect Locations": "#3d5a80",
    "Savoure": "#a34a2e",
}

# Preset camera positions
VIEWS = {
    "ny": {"label": "New York", "center": [40.73, -73.98], "zoom": 11},
    "ca": {"label": "California", "center": [36.7, -119.7], "zoom": 6},
    "us": {"label": "United States", "center": [39.5, -98.0], "zoom": 4},
    "world": {"label": "World", "center": [20.0, 0.0], "zoom": 2},
}

DATA_DIR = Path(__file__).resolve().parent / "data"
OUT_HTML = DATA_DIR / "competitors_map.html"
OUT_DASHBOARD = DATA_DIR / "competitors_dashboard.html"

# Quiet light-gray basemap (legacy Esri canvas — no API key)
BASEMAP_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
)
BASEMAP_ATTR = "Esri, HERE, Garmin, OpenStreetMap contributors"


class ViewSwitcher(MacroElement):
    """Top-left buttons to jump between New York / US / World views."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        (function() {
          var map = {{ this._parent.get_name() }};
          var views = {{ this.views_json }};
          var wrap = L.DomUtil.create('div', 'view-switcher');
          wrap.innerHTML = '<div class="view-switcher__title">View</div>';
          Object.keys(views).forEach(function(key) {
            var btn = L.DomUtil.create('button', 'view-switcher__btn', wrap);
            btn.type = 'button';
            btn.textContent = views[key].label;
            btn.dataset.view = key;
            if (key === '{{ this.default_view }}') btn.classList.add('is-active');
            L.DomEvent.disableClickPropagation(btn);
            L.DomEvent.on(btn, 'click', function() {
              var v = views[key];
              map.setView(v.center, v.zoom, { animate: true });
              wrap.querySelectorAll('.view-switcher__btn').forEach(function(b) {
                b.classList.toggle('is-active', b.dataset.view === key);
              });
            });
          });
          var Custom = L.Control.extend({
            options: { position: 'topleft' },
            onAdd: function() { return wrap; }
          });
          map.addControl(new Custom());
        })();
        {% endmacro %}
        """
    )

    def __init__(self, views: dict, default_view: str = "ny"):
        super().__init__()
        self._name = "ViewSwitcher"
        self.views_json = json.dumps(views)
        self.default_view = default_view


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
        r
        for r in rows
        if r.get("latitude") is not None and r.get("longitude") is not None
    ]

    start = VIEWS["ny"]
    m = folium.Map(
        location=start["center"],
        zoom_start=start["zoom"],
        tiles=None,
        control_scale=True,
        prefer_canvas=True,
    )
    folium.TileLayer(
        tiles=BASEMAP_URL,
        attr=BASEMAP_ATTR,
        name="Basemap",
        control=False,
    ).add_to(m)

    # Quiet chrome — hide noisy zoom attribution clutter where we can
    m.get_root().header.add_child(
        Element(
            """
            <style>
              html, body, .folium-map { height: 100%; margin: 0; }
              .leaflet-container { background: #f2f2f0; font-family: "Segoe UI", Helvetica, Arial, sans-serif; }
              .leaflet-control-attribution {
                font-size: 10px; background: rgba(255,255,255,.72) !important; color: #888;
              }
              .view-switcher {
                background: #fff; border: 1px solid #d8d8d4; border-radius: 10px;
                box-shadow: 0 1px 4px rgba(0,0,0,.12); overflow: hidden; min-width: 138px;
              }
              .view-switcher__title {
                padding: 8px 12px 4px; font-size: 10px; letter-spacing: .08em;
                text-transform: uppercase; color: #8a8a86;
              }
              .view-switcher__btn {
                display: block; width: 100%; border: 0; border-top: 1px solid #eee;
                background: #fff; text-align: left; padding: 9px 12px; cursor: pointer;
                font-size: 13px; color: #222;
              }
              .view-switcher__btn:hover { background: #f6f6f4; }
              .view-switcher__btn.is-active { background: #111; color: #fff; font-weight: 600; }
              .leaflet-control-layers {
                border-radius: 10px !important; border: 1px solid #d8d8d4 !important;
                box-shadow: 0 1px 4px rgba(0,0,0,.12) !important;
              }
              .marker-cluster-small, .marker-cluster-medium, .marker-cluster-large {
                background-color: rgba(17,17,17,.12) !important;
              }
              .marker-cluster-small div, .marker-cluster-medium div, .marker-cluster-large div {
                background-color: rgba(17,17,17,.78) !important; color: #fff !important;
                font-weight: 600;
              }
            </style>
            """
        )
    )

    clusters: dict[str, MarkerCluster] = {}
    for competitor in sorted({r.get("competitor", "Unknown") for r in plottable}):
        clusters[competitor] = MarkerCluster(
            name=competitor,
            options={
                "showCoverageOnHover": False,
                "maxClusterRadius": 48,
                "spiderfyOnMaxZoom": True,
            },
        ).add_to(m)

    for row in plottable:
        competitor = row.get("competitor") or "Unknown"
        color = COMPETITOR_COLORS.get(competitor, "#3388ff")
        popup = (
            f"<b>{row.get('name', '')}</b><br>"
            f"{competitor}<br>"
            f"<span style='color:#666'>{row.get('address', '')}</span>"
        )
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=5,
            weight=0,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.9,
            popup=folium.Popup(popup, max_width=260),
            tooltip=f"{competitor}: {row.get('name', '')}",
        ).add_to(clusters[competitor])

    folium.LayerControl(collapsed=False, position="topright").add_to(m)
    m.add_child(ViewSwitcher(VIEWS, default_view="ny"))
    return m


REGION_LEVEL_CITIES = {
    "california",
    "new york",
    "massachusetts",
    "new jersey",
    "washington dc",
    "north carolina",
    "south carolina",
    "vermont",
    "norway",
    "united states",
    "us",
    "uk",
    "au",
}

US_STATE_NAMES = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "District of Columbia": "DC",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}

US_STATE_ABBREVS = set(US_STATE_NAMES.values())

# Rough bounding boxes: min_lat, max_lat, min_lng, max_lng
STATE_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "NY": (40.49, 45.02, -79.76, -71.85),
    "CA": (32.53, 42.01, -124.48, -114.13),
    "VA": (36.54, 39.47, -83.68, -75.24),
    "NJ": (38.93, 41.36, -75.56, -73.89),
    "MA": (41.24, 42.89, -73.51, -69.86),
    "PA": (39.72, 42.27, -80.52, -74.69),
    "CT": (40.98, 42.05, -73.73, -71.79),
}

DOMESTIC_REGIONS = {"US", "USA", "UNITED STATES"}


def city_from_address(address: str) -> str:
    """Pull a city name out of an OSM/Nominatim-style display address."""
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    abbrev_set = US_STATE_ABBREVS | {"DC"}
    name_set = set(US_STATE_NAMES) | {"New York"}
    state_idx = None
    for i, part in enumerate(parts):
        core = re.sub(r"\s+\d{5}(?:-\d{4})?$", "", part).strip()
        if core in name_set or core in abbrev_set or part in name_set or part in abbrev_set:
            state_idx = i
            break
    if state_idx is None:
        return ""

    for j in range(state_idx - 1, -1, -1):
        part = parts[j]
        if "County" in part:
            continue
        if re.fullmatch(r"\d{5}(?:-\d{4})?", part):
            continue
        if re.fullmatch(r"\d+[A-Za-z]?", part):
            continue
        if part.lower() in {"united states", "usa"}:
            continue
        if "Neighborhood Council" in part or "Council District" in part:
            continue
        if re.match(r"^\d+\s", part):
            continue
        return part
    return ""


def _in_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lat, max_lat, min_lng, max_lng = bbox
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def _state_from_address(address: str) -> str:
    if not address:
        return ""
    match = re.search(r",\s*([A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?|\s*$)", address)
    if match:
        abbr = match.group(1).upper()
        if abbr in US_STATE_ABBREVS:
            return abbr
    upper = address.upper()
    for name, abbr in sorted(US_STATE_NAMES.items(), key=lambda kv: -len(kv[0])):
        if name.upper() in upper:
            return abbr
    if re.search(r"NEW YORK\s+\d{5}", upper):
        return "NY"
    return ""


def _state_from_coords(lat: float, lng: float) -> str:
    for state, bbox in STATE_BBOXES.items():
        if _in_bbox(lat, lng, bbox):
            return state
    return ""


def _normalize_state(raw: str) -> str:
    lower = raw.lower()
    if lower in {"ny", "new york", "new york state"}:
        return "NY"
    if lower in {"ca", "california"}:
        return "CA"
    if lower in {"ma", "massachusetts"}:
        return "MA"
    if lower in {"nj", "new jersey"}:
        return "NJ"
    if lower in {"dc", "district of columbia", "washington dc", "washington, dc"}:
        return "DC"
    if lower in {"nc", "north carolina"}:
        return "NC"
    if lower in {"sc", "south carolina"}:
        return "SC"
    if lower in {"vt", "vermont"}:
        return "VT"
    if lower in {"va", "virginia"}:
        return "VA"
    if len(raw) == 2:
        return raw.upper()
    return US_STATE_NAMES.get(raw, raw)


def _display_city(row: dict) -> str:
    address = row.get("address") or ""
    from_addr_city = city_from_address(address)
    from_addr_state = _state_from_address(address)

    city = (row.get("suburb") or "").strip()
    city_upper = city.upper()
    lat, lng = row.get("latitude"), row.get("longitude")

    if from_addr_state and from_addr_state != "NY" and from_addr_city:
        return from_addr_city

    if city_upper in NY_CITIES:
        if lat is not None and lng is not None:
            if not _in_bbox(float(lat), float(lng), STATE_BBOXES["NY"]) and from_addr_city:
                return from_addr_city
        return "New York" if city_upper == "NEW YORK" else city.title() if city.isupper() else city

    if city and city.lower() not in REGION_LEVEL_CITIES:
        return city
    return from_addr_city or city


def _display_address(row: dict) -> str:
    """Best human-readable address for the database table."""
    address = (row.get("address") or "").strip()
    city = (row.get("suburb") or "").strip()
    region = (row.get("region") or "").strip()
    state = (row.get("state") or "").strip()

    if address:
        return address

    parts = [p for p in (city, state or region) if p]
    return ", ".join(parts)


def _display_area(row: dict) -> str:
    """City / state / country — never a street address."""
    city = _display_city(row)
    state = _infer_state(row)
    region = (row.get("region") or "").strip()
    if state:
        label = f"{city}, {state}" if city else state
    elif region and region.upper() not in DOMESTIC_REGIONS:
        label = f"{city}, {region}" if city else region
    elif city:
        label = city
    else:
        label = ""

    address = _display_address(row).strip()
    if label and address and label.lower() == address.lower():
        return ""
    return label


def _infer_state(row: dict) -> str:
    """Normalize/infer US state; avoid false NY matches for Albany AU, Glendale CA, etc."""
    raw = (row.get("state") or "").strip()
    if raw:
        return _normalize_state(raw)

    region = (row.get("region") or "").strip().upper()
    if region and region not in DOMESTIC_REGIONS:
        return ""

    address = row.get("address") or ""
    from_address = _state_from_address(address)
    if from_address:
        return from_address

    lat, lng = row.get("latitude"), row.get("longitude")
    if lat is not None and lng is not None:
        from_coords = _state_from_coords(float(lat), float(lng))
        if from_coords:
            return from_coords

    address_upper = address.upper()
    if ", NY" in address_upper or re.search(r"\bNY\b", address_upper):
        return "NY"
    if "BROOKLYN" in address_upper or "QUEENS, NY" in address_upper:
        return "NY"
    if "NEW YORK, NEW YORK" in address_upper or re.search(r"NEW YORK\s+\d{5}", address_upper):
        return "NY"

    city = (row.get("suburb") or "").strip().upper()
    if city in NY_CITIES:
        if lat is not None and lng is not None:
            ny_bbox = STATE_BBOXES["NY"]
            if _in_bbox(float(lat), float(lng), ny_bbox):
                return "NY"
        elif region in DOMESTIC_REGIONS and any(
            token in address_upper for token in ("NEW YORK", "BROOKLYN", "QUEENS", "BRONX", "MANHATTAN")
        ):
            return "NY"

    if region == "NORWAY":
        return ""
    return ""


NY_CITIES = {
    "NEW YORK",
    "BROOKLYN",
    "QUEENS",
    "BRONX",
    "THE BRONX",
    "MANHATTAN",
    "STATEN ISLAND",
    "LONG ISLAND CITY",
    "ASTORIA",
    "WILLIAMSBURG",
    "GLENDALE",
    "HUDSON",
    "YONKERS",
    "WHITE PLAINS",
    "BUFFALO",
    "ROCHESTER",
    "ALBANY",
    "SYRACUSE",
    "FLUSHING",
    "JAMAICA",
    "FOREST HILLS",
    "RIVERDALE",
    "HARLEM",
    "BUSHWICK",
    "PARK SLOPE",
    "DUMBO",
    "GREENPOINT",
    "BEDFORD-STUYVESANT",
    "CROWN HEIGHTS",
    "FORT GREENE",
    "COBBLE HILL",
    "CARROLL GARDENS",
    "RED HOOK",
    "SUNSET PARK",
    "BAY RIDGE",
    "BOROUGH PARK",
    "FLATBUSH",
    "PROSPECT HEIGHTS",
    "GOWANUS",
    "BOERUM HILL",
    "DOWNTOWN BROOKLYN",
    "MIDTOWN",
    "SOHO",
    "NOHO",
    "TRIBECA",
    "CHELSEA",
    "WEST VILLAGE",
    "EAST VILLAGE",
    "LOWER EAST SIDE",
    "UPPER WEST SIDE",
    "UPPER EAST SIDE",
    "WASHINGTON HEIGHTS",
    "INWOOD",
}


def build_dashboard(rows: list[dict]) -> str:
    """Static HTML dashboard: Map tab + searchable competitor database table."""
    table_rows = []
    for r in rows:
        phone, email = row_phone_email(r)
        table_rows.append(
            {
                "competitor": r.get("competitor") or "",
                "name": r.get("name") or "",
                "address": _display_address(r),
                "area": _display_area(r),
                "city": _display_city(r),
                "region": r.get("region") or "",
                "state": _infer_state(r),
                "type": r.get("venue_type") or "",
                "phone": phone,
                "email": email,
                "website": r.get("website") or "",
                "lat": r.get("latitude"),
                "lng": r.get("longitude"),
            }
        )
    payload = json.dumps(table_rows, ensure_ascii=False)
    total = len(table_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Astra Competitor + Stock List Navigator</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600&family=Newsreader:opsz,wght@6..72,400&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --paper: #eceeea;
      --panel: #fbfcfa;
      --hover: #f2f4ef;
      --ink: #101a1e;
      --ink-2: #5c6b70;
      --ink-3: #8d9a9e;
      --rule: #d5dbd5;
      --rule-soft: #e7eae4;
      --c-non: #136f63;
      --c-vil: #8a6b16;
      --c-uni: #8b3a62;
      --c-pro: #3d5a80;
      --c-sav: #a34a2e;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0; height: 100%;
      font-family: Archivo, sans-serif;
      font-size: 15px;
      color: var(--ink);
      background: var(--paper);
    }}
    .app {{ display: flex; flex-direction: column; height: 100%; }}
    header {{
      flex: none; display: flex; align-items: center; gap: 18px;
      padding: 14px 20px; background: var(--panel);
      border-bottom: 1px solid var(--rule);
    }}
    header h1 {{
      margin: 0;
      font-family: Newsreader, serif;
      font-weight: 400;
      font-size: 25px;
      color: var(--ink);
    }}
    .tabs {{ display: flex; gap: 6px; margin-left: auto; }}
    .tab {{
      border: 1px solid var(--rule); background: transparent;
      padding: 7px 12px; cursor: pointer;
      font-family: inherit; font-size: 13.5px; color: var(--ink-2);
    }}
    .tab:hover {{ background: var(--hover); color: var(--ink); }}
    .tab.is-active {{
      color: #fff; background: var(--ink); border-color: var(--ink);
    }}
    main {{ flex: 1; min-height: 0; position: relative; }}
    .panel {{ position: absolute; inset: 0; display: none; }}
    .panel.is-active {{ display: flex; flex-direction: column; }}

    .map-shell {{
      flex: 1; min-height: 0; display: flex; flex-direction: column;
      padding: 14px 20px 20px;
    }}
    .map-legend {{
      flex: none; display: flex; flex-wrap: wrap; gap: 14px 18px;
      align-items: center; margin-bottom: 10px;
      font-size: 12px; color: var(--ink-2);
    }}
    .map-legend .dot {{
      width: 8px; height: 8px; border-radius: 50%;
      display: inline-block; margin-right: 6px; vertical-align: middle;
    }}
    .map-shell iframe {{
      flex: 1; width: 100%; min-height: 0; display: block;
      border: 1px solid var(--rule); background: var(--panel);
    }}

    .db-toolbar {{
      flex: none; display: flex; flex-wrap: wrap; gap: 12px 16px; align-items: flex-end;
      padding: 14px 20px; background: var(--panel);
      border-bottom: 1px solid var(--rule);
    }}
    .db-toolbar .field {{
      display: flex; flex-direction: column; gap: 5px;
    }}
    .db-toolbar .field > span {{
      font-size: 12px; color: var(--ink-2);
    }}
    .db-toolbar select, .db-toolbar input {{
      font: inherit; font-size: 13.5px; color: var(--ink);
      border: 1px solid var(--rule); border-radius: 0;
      padding: 8px 10px; min-width: 160px; background: var(--panel);
    }}
    .db-toolbar input {{ min-width: 220px; }}
    .chips {{
      display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
    }}
    .chip {{
      display: inline-flex; align-items: center; gap: 6px;
      border: 1px solid var(--rule); background: var(--panel);
      padding: 7px 11px; cursor: pointer;
      font-family: inherit; font-size: 12.5px; color: var(--ink);
    }}
    .chip:hover {{ background: var(--hover); }}
    .chip.is-active {{
      background: var(--ink); border-color: var(--ink); color: #fff;
    }}
    .chip .dot {{
      width: 7px; height: 7px; border-radius: 50%; flex: none;
    }}
    .chip.is-active .dot {{ box-shadow: 0 0 0 1px rgba(255,255,255,.35); }}
    .chip-count {{
      font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums;
    }}
    .chip.is-active .chip-count {{ color: rgba(255,255,255,.75); }}

    .share {{
      flex: none; padding: 12px 20px 10px; background: var(--panel);
      border-bottom: 1px solid var(--rule);
    }}
    .share-bar {{
      display: flex; width: 100%; height: 7px; background: var(--rule-soft);
      overflow: hidden;
    }}
    .share-bar .seg {{
      height: 100%; min-width: 0;
      transition: flex-basis .25s ease;
    }}
    .share-bar .seg-non {{ background: var(--c-non); }}
    .share-bar .seg-vil {{ background: var(--c-vil); }}
    .share-bar .seg-uni {{ background: var(--c-uni); }}
    .share-bar .seg-pro {{ background: var(--c-pro); }}
    .share-bar .seg-sav {{ background: var(--c-sav); }}
    .share-label {{
      margin-top: 8px; font-size: 13.5px; color: var(--ink-2);
      font-variant-numeric: tabular-nums;
    }}
    .share-label strong {{
      color: var(--ink); font-weight: 600;
      font-variant-numeric: tabular-nums;
    }}
    @media (prefers-reduced-motion: reduce) {{
      .share-bar .seg {{ transition: none; }}
    }}

    .table-wrap {{ flex: 1; overflow: auto; background: var(--panel); }}
    table.db-table {{
      width: 100%; border-collapse: collapse;
      table-layout: fixed;
      font-size: 12.5px; background: var(--panel);
    }}
    table.db-table col.col-brand {{ width: 6.5%; }}
    table.db-table col.col-name {{ width: 13%; }}
    table.db-table col.col-address {{ width: 21%; }}
    table.db-table col.col-area {{ width: 9%; }}
    table.db-table col.col-type {{ width: 10%; }}
    table.db-table col.col-phone {{ width: 11.5%; }}
    table.db-table col.col-email {{ width: 14%; }}
    table.db-table col.col-website {{ width: 12%; }}
    table.db-table col.col-map {{ width: 3%; }}
    thead th {{
      position: sticky; top: 0; z-index: 1;
      background: var(--paper); text-align: left;
      font-size: 11.5px; font-weight: 600; color: var(--ink-2);
      padding: 8px 10px; border-bottom: 1px solid var(--rule);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    tbody td {{
      padding: 8px 10px; border-bottom: 1px solid var(--rule-soft);
      vertical-align: top;
    }}
    tbody tr:hover {{ background: var(--hover); }}
    td.col-brand {{
      font-size: 11.5px; color: var(--ink);
      border-left: 3px solid transparent;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    tr.c-NON td.col-brand {{ border-left-color: var(--c-non); }}
    tr.c-Villbrygg td.col-brand {{ border-left-color: var(--c-vil); }}
    tr.c-Unified td.col-brand {{ border-left-color: var(--c-uni); }}
    tr.c-Prospects td.col-brand {{ border-left-color: var(--c-pro); }}
    tr.c-Savoure td.col-brand {{ border-left-color: var(--c-sav); }}
    td.col-name {{
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    td.col-address {{
      white-space: normal; line-height: 1.35;
      word-break: break-word;
    }}
    td.col-area,
    td.col-type {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--ink-2);
      font-size: 12px;
    }}
    td.col-phone, th.col-phone {{
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
      letter-spacing: -0.02em;
      font-size: 12px;
      overflow: visible;
    }}
    td.col-email, td.col-website {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 12px;
    }}
    td.col-email a, td.col-website a {{
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .name-cell {{ font-weight: 500; }}
    .name-cell .addr-sub,
    .name-cell .meta-line {{ display: none; }}
    .muted {{ color: var(--ink-3); }}
    .empty {{
      padding: 48px 20px; text-align: center; color: var(--ink-2); font-size: 15px;
    }}
    .empty p {{ margin: 0 0 6px; }}
    .empty .hint {{ font-size: 12.5px; color: var(--ink-3); }}
    a.maps {{
      color: var(--ink); text-decoration: underline;
      text-underline-offset: 2px; font-size: 12.5px; white-space: nowrap;
    }}
    a.maps:hover {{ color: var(--ink-2); }}

    .tab:focus-visible,
    .chip:focus-visible,
    .db-toolbar select:focus-visible,
    .db-toolbar input:focus-visible,
    a.maps:focus-visible {{
      outline: 2px solid var(--ink);
      outline-offset: 2px;
    }}

    @media (max-width: 860px) {{
      thead {{ display: none; }}
      table, tbody, tr, td {{ display: block; width: 100%; }}
      tbody tr {{
        border-bottom: 1px solid var(--rule);
        border-left: 3px solid transparent;
        padding: 12px 14px 12px 12px;
        margin: 0;
      }}
      tr.c-NON {{ border-left-color: var(--c-non); }}
      tr.c-Villbrygg {{ border-left-color: var(--c-vil); }}
      tr.c-Unified {{ border-left-color: var(--c-uni); }}
      tr.c-Prospects {{ border-left-color: var(--c-pro); }}
      tr.c-Savoure {{ border-left-color: var(--c-sav); }}
      tbody td {{
        border: 0; padding: 0; width: auto !important;
      }}
      td.col-brand,
      td.col-address,
      td.col-area,
      td.col-type,
      td.col-phone,
      td.col-email,
      td.col-website,
      td.col-map {{ display: none; }}
      .name-cell .meta-line {{
        display: block; margin-top: 4px;
        font-weight: 400; font-size: 12px;
        color: var(--ink-2); line-height: 1.4;
      }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <header>
      <h1>Astra Competitor + Stock List Navigator</h1>
      <nav class="tabs" role="tablist">
        <button class="tab is-active" type="button" data-tab="map">Map</button>
        <button class="tab" type="button" data-tab="db">Competitor database</button>
      </nav>
    </header>
    <main>
      <section id="panel-map" class="panel is-active" role="tabpanel">
        <div class="map-shell">
          <div class="map-legend" aria-label="Brand legend">
            <span><span class="dot" style="background:var(--c-non)"></span>NON</span>
            <span><span class="dot" style="background:var(--c-vil)"></span>Villbrygg</span>
            <span><span class="dot" style="background:var(--c-uni)"></span>Unified Ferments</span>
            <span><span class="dot" style="background:var(--c-pro)"></span>Researched Prospect Locations</span>
            <span><span class="dot" style="background:var(--c-sav)"></span>Savoure</span>
          </div>
          <iframe src="competitors_map.html" title="Competitor map"></iframe>
        </div>
      </section>
      <section id="panel-db" class="panel" role="tabpanel">
        <div class="db-toolbar">
          <div class="field">
            <span>Competitor</span>
            <div class="chips" role="group" aria-label="Competitor">
              <button type="button" class="chip is-active" data-competitor="">All <span class="chip-count" data-count-for="">0</span></button>
              <button type="button" class="chip" data-competitor="NON"><span class="dot" style="background:var(--c-non)"></span>NON <span class="chip-count" data-count-for="NON">0</span></button>
              <button type="button" class="chip" data-competitor="Villbrygg"><span class="dot" style="background:var(--c-vil)"></span>Villbrygg <span class="chip-count" data-count-for="Villbrygg">0</span></button>
              <button type="button" class="chip" data-competitor="Unified Ferments"><span class="dot" style="background:var(--c-uni)"></span>Unified Ferments <span class="chip-count" data-count-for="Unified Ferments">0</span></button>
              <button type="button" class="chip" data-competitor="Researched Prospect Locations"><span class="dot" style="background:var(--c-pro)"></span>Prospects <span class="chip-count" data-count-for="Researched Prospect Locations">0</span></button>
              <button type="button" class="chip" data-competitor="Savoure"><span class="dot" style="background:var(--c-sav)"></span>Savoure <span class="chip-count" data-count-for="Savoure">0</span></button>
            </div>
          </div>
          <label class="field"><span>State / region</span>
            <select id="placeFilter">
              <option value="">All places</option>
            </select>
          </label>
          <label class="field"><span>Search</span>
            <input id="searchInput" type="search" placeholder="Name, address, phone, email…" autocomplete="off" />
          </label>
        </div>
        <div class="share">
          <div class="share-bar" id="shareBar" aria-hidden="true">
            <span class="seg seg-non" id="segNon" style="flex:0 0 0%"></span>
            <span class="seg seg-vil" id="segVil" style="flex:0 0 0%"></span>
            <span class="seg seg-uni" id="segUni" style="flex:0 0 0%"></span>
            <span class="seg seg-pro" id="segPro" style="flex:0 0 0%"></span>
            <span class="seg seg-sav" id="segSav" style="flex:0 0 0%"></span>
          </div>
          <div class="share-label"><strong id="shareN">0</strong> of {total:,} locations</div>
        </div>
        <div class="table-wrap">
          <table class="db-table">
            <colgroup>
              <col class="col-brand" />
              <col class="col-name" />
              <col class="col-address" />
              <col class="col-area" />
              <col class="col-type" />
              <col class="col-phone" />
              <col class="col-email" />
              <col class="col-website" />
              <col class="col-map" />
            </colgroup>
            <thead>
              <tr>
                <th>Brand</th>
                <th>Name</th>
                <th>Address</th>
                <th>City / Region</th>
                <th>Type</th>
                <th class="col-phone">Phone</th>
                <th>Email</th>
                <th>Website</th>
                <th></th>
              </tr>
            </thead>
            <tbody id="tableBody"></tbody>
          </table>
          <div class="empty" id="emptyState" hidden>
            <p>No locations match these filters.</p>
            <p class="hint">Clear the search box or switch back to all brands.</p>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const DATA = {payload};
    const TOTAL = DATA.length;

    const tabs = document.querySelectorAll('.tab');
    const panels = {{
      map: document.getElementById('panel-map'),
      db: document.getElementById('panel-db'),
    }};
    tabs.forEach((tab) => {{
      tab.addEventListener('click', () => {{
        tabs.forEach((t) => t.classList.toggle('is-active', t === tab));
        Object.entries(panels).forEach(([key, el]) => {{
          el.classList.toggle('is-active', key === tab.dataset.tab);
        }});
      }});
    }});

    const chips = Array.from(document.querySelectorAll('.chip'));
    const placeFilter = document.getElementById('placeFilter');
    const searchInput = document.getElementById('searchInput');
    const tbody = document.getElementById('tableBody');
    const emptyState = document.getElementById('emptyState');
    const shareN = document.getElementById('shareN');
    const segNon = document.getElementById('segNon');
    const segVil = document.getElementById('segVil');
    const segUni = document.getElementById('segUni');
    const segPro = document.getElementById('segPro');
    const segSav = document.getElementById('segSav');

    let activeCompetitor = '';

    const STATE_LABELS = {{
      NY: 'New York',
      CA: 'California',
      CT: 'Connecticut',
      MA: 'Massachusetts',
      NJ: 'New Jersey',
      DC: 'Washington DC',
      NC: 'North Carolina',
      SC: 'South Carolina',
      VT: 'Vermont',
    }};

    // Place filter: US states (esp. New York = whole state) + country regions
    const placeOptions = [];
    const states = Array.from(new Set(DATA.map((r) => r.state).filter(Boolean))).sort((a, b) => {{
      const la = STATE_LABELS[a] || a;
      const lb = STATE_LABELS[b] || b;
      return la.localeCompare(lb);
    }});
    states.forEach((state) => {{
      placeOptions.push({{
        value: 'state:' + state,
        label: STATE_LABELS[state] || state,
      }});
    }});
    const regions = Array.from(new Set(DATA.map((r) => r.region).filter(Boolean))).sort((a, b) => a.localeCompare(b));
    regions.forEach((region) => {{
      placeOptions.push({{
        value: 'region:' + region,
        label: region,
      }});
    }});
    placeOptions.forEach((item) => {{
      const opt = document.createElement('option');
      opt.value = item.value;
      opt.textContent = item.label;
      placeFilter.appendChild(opt);
    }});

    function brandLabel(name) {{
      const SHORT = {{
        'Unified Ferments': 'UF',
        'Researched Prospect Locations': 'Prospects',
      }};
      return SHORT[name] || name;
    }}

    function titleAttr(value) {{
      if (!value) return '';
      return ` title="${{escapeHtml(value)}}"`;
    }}

    function rowClass(name) {{
      if (name === 'NON') return 'c-NON';
      if (name === 'Villbrygg') return 'c-Villbrygg';
      if (name === 'Unified Ferments') return 'c-Unified';
      if (name === 'Researched Prospect Locations') return 'c-Prospects';
      if (name === 'Savoure') return 'c-Savoure';
      return '';
    }}

    function escapeHtml(s) {{
      return String(s)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function matchesPlaceSearch(row) {{
      const place = placeFilter.value;
      const q = searchInput.value.trim().toLowerCase();
      if (place) {{
        if (place.startsWith('state:')) {{
          if (row.state !== place.slice(6)) return false;
        }} else if (place.startsWith('region:')) {{
          if (row.region !== place.slice(7)) return false;
        }}
      }}
      if (!q) return true;
      const hay = [row.competitor, row.name, row.address, row.area, row.city, row.state, row.region, row.type, row.phone, row.email, row.website]
        .join(' ').toLowerCase();
      return hay.includes(q);
    }}

    function filtered() {{
      return DATA.filter((row) => {{
        if (activeCompetitor && row.competitor !== activeCompetitor) return false;
        return matchesPlaceSearch(row);
      }});
    }}

    function updateChipCounts() {{
      const base = DATA.filter(matchesPlaceSearch);
      const counts = {{
        '': base.length,
        'NON': 0,
        'Villbrygg': 0,
        'Unified Ferments': 0,
        'Researched Prospect Locations': 0,
        'Savoure': 0,
      }};
      base.forEach((row) => {{
        if (counts[row.competitor] != null) counts[row.competitor] += 1;
      }});
      document.querySelectorAll('[data-count-for]').forEach((el) => {{
        const key = el.getAttribute('data-count-for');
        el.textContent = (counts[key] || 0).toLocaleString();
      }});
    }}

    function updateShare(rows) {{
      const n = rows.length;
      shareN.textContent = n.toLocaleString();
      const brands = {{
        NON: 0,
        Villbrygg: 0,
        'Unified Ferments': 0,
        'Researched Prospect Locations': 0,
        'Savoure': 0,
      }};
      rows.forEach((row) => {{
        if (brands[row.competitor] != null) brands[row.competitor] += 1;
      }});
      const pct = (v) => (n ? ((v / n) * 100) : 0);
      segNon.style.flex = '0 0 ' + pct(brands.NON) + '%';
      segVil.style.flex = '0 0 ' + pct(brands.Villbrygg) + '%';
      segUni.style.flex = '0 0 ' + pct(brands['Unified Ferments']) + '%';
      segPro.style.flex = '0 0 ' + pct(brands['Researched Prospect Locations']) + '%';
      segSav.style.flex = '0 0 ' + pct(brands.Savoure) + '%';
    }}

    function render() {{
      const rows = filtered();
      updateChipCounts();
      updateShare(rows);
      emptyState.hidden = rows.length > 0;
      tbody.innerHTML = rows.map((row) => {{
        const maps = (row.lat != null && row.lng != null)
          ? `<a class="maps" href="https://www.google.com/maps?q=${{row.lat}},${{row.lng}}" target="_blank" rel="noopener">Map</a>`
          : '';
        const addr = escapeHtml(row.address || '');
        const meta = [row.address, row.area, row.type, row.phone, row.email, row.website]
          .filter(Boolean).map(escapeHtml).join(' · ');
        const phone = row.phone
          ? `<span class="phone-num">${{escapeHtml(row.phone)}}</span>`
          : '<span class="muted">—</span>';
        let email = '<span class="muted">—</span>';
        if (row.email) {{
          if (row.email.includes('@') && !row.email.startsWith('@')) {{
            email = `<a class="maps" href="mailto:${{escapeHtml(row.email)}}"${{titleAttr(row.email)}}>${{escapeHtml(row.email)}}</a>`;
          }} else {{
            email = `<span${{titleAttr(row.email)}}>${{escapeHtml(row.email)}}</span>`;
          }}
        }}
        let website = '<span class="muted">—</span>';
        if (row.website) {{
          const url = row.website.startsWith('http') ? row.website : 'https://' + row.website;
          const label = row.website.replace(/^https?:\\/\\//, '').replace(/\\/$/, '');
          website = `<a class="maps" href="${{escapeHtml(url)}}" target="_blank" rel="noopener"${{titleAttr(row.website)}}>${{escapeHtml(label)}}</a>`;
        }}
        return `<tr class="${{rowClass(row.competitor)}}">
          <td class="col-brand"${{titleAttr(row.competitor)}}>${{escapeHtml(brandLabel(row.competitor))}}</td>
          <td class="name-cell col-name"${{titleAttr(row.name)}}>${{escapeHtml(row.name)}}<span class="meta-line">${{meta}}</span></td>
          <td class="col-address"${{titleAttr(row.address)}}>${{addr || '<span class="muted">—</span>'}}</td>
          <td class="col-area"${{titleAttr(row.area)}}>${{row.area ? escapeHtml(row.area) : '<span class="muted">—</span>'}}</td>
          <td class="col-type muted"${{titleAttr(row.type)}}>${{escapeHtml(row.type) || '—'}}</td>
          <td class="col-phone"${{titleAttr(row.phone)}}>${{phone}}</td>
          <td class="col-email">${{email}}</td>
          <td class="col-website">${{website}}</td>
          <td class="col-map">${{maps}}</td>
        </tr>`;
      }}).join('');
    }}

    chips.forEach((chip) => {{
      chip.addEventListener('click', () => {{
        activeCompetitor = chip.dataset.competitor || '';
        chips.forEach((c) => c.classList.toggle('is-active', c === chip));
        render();
      }});
    }});
    placeFilter.addEventListener('change', render);
    searchInput.addEventListener('input', render);
    render();
  </script>
</body>
</html>
"""


def main() -> None:
    rows = load_locations()
    if not rows:
        raise SystemExit(f"No *_locations.json files found in {DATA_DIR}. Run a scraper first.")
    m = build_map(rows)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    m.save(str(OUT_HTML))
    OUT_DASHBOARD.write_text(build_dashboard(rows), encoding="utf-8")
    by_comp: dict[str, int] = {}
    for row in rows:
        c = row.get("competitor") or "?"
        by_comp[c] = by_comp.get(c, 0) + 1
    plotted = sum(
        1 for r in rows if r.get("latitude") is not None and r.get("longitude") is not None
    )
    print(f"Mapped {plotted} locations -> {OUT_HTML}")
    print(f"Dashboard -> {OUT_DASHBOARD}")
    print("By competitor:", by_comp)
    print("Open competitors_dashboard.html (tabs: Map | Competitor database)")


if __name__ == "__main__":
    main()
