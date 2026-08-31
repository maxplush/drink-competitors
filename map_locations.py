"""Merge competitor location files and render a clean Folium map."""

from __future__ import annotations

import json
from pathlib import Path

import folium
from branca.element import Element, MacroElement, Template
from folium.plugins import MarkerCluster

COMPETITOR_COLORS = {
    "NON": "#1f9d57",
    "Villbrygg": "#c45c26",
    "Unified Ferments": "#2f5d8c",
}

# Preset camera positions: New York first, then US, then world
VIEWS = {
    "ny": {"label": "New York", "center": [40.73, -73.98], "zoom": 11},
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


def _display_address(row: dict) -> str:
    """Best human-readable address for the database table."""
    address = (row.get("address") or "").strip()
    city = (row.get("suburb") or "").strip()
    region = (row.get("region") or "").strip()
    state = (row.get("state") or "").strip()

    if address:
        # If address is only "City, Region", keep it; otherwise prefer the street line.
        return address

    parts = [p for p in (city, state or region) if p]
    return ", ".join(parts)


def build_dashboard(rows: list[dict]) -> str:
    """Static HTML dashboard: Map tab + searchable competitor database table."""
    table_rows = []
    for r in rows:
        table_rows.append(
            {
                "competitor": r.get("competitor") or "",
                "name": r.get("name") or "",
                "address": _display_address(r),
                "city": r.get("suburb") or "",
                "region": r.get("region") or "",
                "state": r.get("state") or "",
                "type": r.get("venue_type") or "",
                "lat": r.get("latitude"),
                "lng": r.get("longitude"),
            }
        )
    competitors = sorted({r["competitor"] for r in table_rows if r["competitor"]})
    payload = json.dumps(table_rows, ensure_ascii=False)
    competitor_options = "\n".join(
        f'<option value="{c}">{c}</option>' for c in competitors
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Competitor Locations</title>
  <style>
    :root {{
      --ink: #141414;
      --muted: #6b6b66;
      --line: #e4e4df;
      --bg: #f7f7f3;
      --panel: #ffffff;
      --accent: #111;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0; height: 100%;
      font-family: "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif;
      color: var(--ink); background: var(--bg);
    }}
    .app {{ display: flex; flex-direction: column; height: 100%; }}
    header {{
      flex: none; display: flex; align-items: center; gap: 18px;
      padding: 12px 18px; background: var(--panel);
      border-bottom: 1px solid var(--line);
    }}
    header h1 {{
      margin: 0; font-size: 15px; font-weight: 650; letter-spacing: .02em;
    }}
    .tabs {{ display: flex; gap: 4px; margin-left: auto; }}
    .tab {{
      border: 1px solid transparent; background: transparent;
      padding: 8px 14px; border-radius: 999px; cursor: pointer;
      font-size: 13px; color: var(--muted);
    }}
    .tab:hover {{ color: var(--ink); background: #efefe9; }}
    .tab.is-active {{
      color: #fff; background: var(--accent); border-color: var(--accent);
    }}
    main {{ flex: 1; min-height: 0; position: relative; }}
    .panel {{ position: absolute; inset: 0; display: none; }}
    .panel.is-active {{ display: flex; flex-direction: column; }}
    #panel-map iframe {{
      border: 0; width: 100%; height: 100%; display: block; background: #f2f2f0;
    }}
    .db-toolbar {{
      flex: none; display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
      padding: 12px 16px; background: var(--panel); border-bottom: 1px solid var(--line);
    }}
    .db-toolbar label {{
      font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted);
      display: flex; flex-direction: column; gap: 4px;
    }}
    .db-toolbar select, .db-toolbar input {{
      font: inherit; font-size: 13px; color: var(--ink);
      border: 1px solid var(--line); border-radius: 8px;
      padding: 8px 10px; min-width: 180px; background: #fff;
    }}
    .db-toolbar input {{ min-width: 240px; }}
    .count {{
      margin-left: auto; font-size: 13px; color: var(--muted);
    }}
    .table-wrap {{ flex: 1; overflow: auto; }}
    table {{
      width: 100%; border-collapse: collapse; font-size: 13px; background: var(--panel);
    }}
    thead th {{
      position: sticky; top: 0; z-index: 1;
      background: #f0f0eb; text-align: left; font-weight: 600;
      padding: 10px 12px; border-bottom: 1px solid var(--line);
      white-space: nowrap;
    }}
    tbody td {{
      padding: 9px 12px; border-bottom: 1px solid #efefe9;
      vertical-align: top;
    }}
    td.col-address {{
      min-width: 260px;
      max-width: 420px;
      white-space: normal;
      line-height: 1.35;
      font-weight: 500;
    }}
    th.col-address {{ min-width: 260px; }}
    .name-cell {{ font-weight: 600; }}
    .name-cell .addr-sub {{
      display: block; margin-top: 3px; font-weight: 400;
      color: var(--muted); font-size: 12px; line-height: 1.35;
    }}
    tbody tr:hover {{ background: #fafaf7; }}
    .comp-pill {{
      display: inline-block; padding: 2px 8px; border-radius: 999px;
      font-size: 12px; font-weight: 600; background: #eee; white-space: nowrap;
    }}
    .comp-NON {{ background: #e5f6ec; color: #1f9d57; }}
    .comp-Villbrygg {{ background: #f8ebe3; color: #c45c26; }}
    .comp-Unified {{ background: #e7eef5; color: #2f5d8c; }}
    .muted {{ color: var(--muted); }}
    .empty {{
      padding: 48px 20px; text-align: center; color: var(--muted); font-size: 14px;
    }}
    a.maps {{
      color: #2f5d8c; text-decoration: none; font-size: 12px; white-space: nowrap;
    }}
    a.maps:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="app">
    <header>
      <h1>Competitor Locations</h1>
      <nav class="tabs" role="tablist">
        <button class="tab is-active" type="button" data-tab="map">Map</button>
        <button class="tab" type="button" data-tab="db">Competitor database</button>
      </nav>
    </header>
    <main>
      <section id="panel-map" class="panel is-active" role="tabpanel">
        <iframe src="competitors_map.html" title="Competitor map"></iframe>
      </section>
      <section id="panel-db" class="panel" role="tabpanel">
        <div class="db-toolbar">
          <label>Competitor
            <select id="competitorFilter">
              <option value="">All competitors</option>
              {competitor_options}
            </select>
          </label>
          <label>Search
            <input id="searchInput" type="search" placeholder="Name, address, city…" autocomplete="off" />
          </label>
          <div class="count" id="resultCount"></div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Competitor</th>
                <th>Name</th>
                <th class="col-address">Street address</th>
                <th>City</th>
                <th>Region</th>
                <th>Type</th>
                <th></th>
              </tr>
            </thead>
            <tbody id="tableBody"></tbody>
          </table>
          <div class="empty" id="emptyState" hidden>No locations match your filters.</div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const DATA = {payload};

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

    const competitorFilter = document.getElementById('competitorFilter');
    const searchInput = document.getElementById('searchInput');
    const tbody = document.getElementById('tableBody');
    const emptyState = document.getElementById('emptyState');
    const resultCount = document.getElementById('resultCount');

    function pillClass(name) {{
      if (name === 'NON') return 'comp-NON';
      if (name === 'Villbrygg') return 'comp-Villbrygg';
      if (name === 'Unified Ferments') return 'comp-Unified';
      return '';
    }}

    function escapeHtml(s) {{
      return String(s)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function filtered() {{
      const comp = competitorFilter.value;
      const q = searchInput.value.trim().toLowerCase();
      return DATA.filter((row) => {{
        if (comp && row.competitor !== comp) return false;
        if (!q) return true;
        const hay = [row.competitor, row.name, row.address, row.city, row.region, row.type]
          .join(' ').toLowerCase();
        return hay.includes(q);
      }});
    }}

    function render() {{
      const rows = filtered();
      resultCount.textContent = rows.length.toLocaleString() + ' location' + (rows.length === 1 ? '' : 's');
      emptyState.hidden = rows.length > 0;
      tbody.innerHTML = rows.map((row) => {{
        const maps = (row.lat != null && row.lng != null)
          ? `<a class="maps" href="https://www.google.com/maps?q=${{row.lat}},${{row.lng}}" target="_blank" rel="noopener">Map</a>`
          : '';
        const addr = escapeHtml(row.address || '');
        return `<tr>
          <td><span class="comp-pill ${{pillClass(row.competitor)}}">${{escapeHtml(row.competitor)}}</span></td>
          <td class="name-cell">${{escapeHtml(row.name)}}<span class="addr-sub">${{addr}}</span></td>
          <td class="col-address">${{addr || '<span class="muted">—</span>'}}</td>
          <td>${{escapeHtml(row.city)}}</td>
          <td>${{escapeHtml(row.region)}}</td>
          <td class="muted">${{escapeHtml(row.type)}}</td>
          <td>${{maps}}</td>
        </tr>`;
      }}).join('');
    }}

    competitorFilter.addEventListener('change', render);
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
