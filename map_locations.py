"""Merge competitor location files and render a clean Folium map."""

from __future__ import annotations

import json
from pathlib import Path

import folium
from branca.element import Element, MacroElement, Template
from folium.plugins import MarkerCluster

COMPETITOR_COLORS = {
    "NON": "#136f63",
    "Villbrygg": "#8a6b16",
    "Unified Ferments": "#8b3a62",
    "Researched Prospect Locations": "#3d5a80",
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
    payload = json.dumps(table_rows, ensure_ascii=False)
    total = len(table_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Competitor Locations</title>
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
    table {{
      width: 100%; border-collapse: collapse;
      font-size: 13.5px; background: var(--panel);
    }}
    thead th {{
      position: sticky; top: 0; z-index: 1;
      background: var(--paper); text-align: left;
      font-size: 12.5px; font-weight: 600; color: var(--ink-2);
      padding: 10px 14px; border-bottom: 1px solid var(--rule);
      white-space: nowrap;
    }}
    tbody td {{
      padding: 10px 14px; border-bottom: 1px solid var(--rule-soft);
      vertical-align: top;
    }}
    tbody tr:hover {{ background: var(--hover); }}
    td.col-brand {{
      font-size: 12.5px; color: var(--ink);
      border-left: 3px solid transparent;
      white-space: nowrap;
    }}
    tr.c-NON td.col-brand {{ border-left-color: var(--c-non); }}
    tr.c-Villbrygg td.col-brand {{ border-left-color: var(--c-vil); }}
    tr.c-Unified td.col-brand {{ border-left-color: var(--c-uni); }}
    tr.c-Prospects td.col-brand {{ border-left-color: var(--c-pro); }}
    td.col-address {{
      min-width: 240px; max-width: 400px;
      white-space: normal; line-height: 1.4;
    }}
    th.col-address {{ min-width: 240px; }}
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
      tbody td {{
        border: 0; padding: 0; width: auto !important;
      }}
      td.col-brand,
      td.col-address,
      td.col-city,
      td.col-region,
      td.col-type,
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
      <h1>Competitor locations</h1>
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
            </div>
          </div>
          <label class="field"><span>Region</span>
            <select id="regionFilter">
              <option value="">All regions</option>
            </select>
          </label>
          <label class="field"><span>Search</span>
            <input id="searchInput" type="search" placeholder="Name, address, city…" autocomplete="off" />
          </label>
        </div>
        <div class="share">
          <div class="share-bar" id="shareBar" aria-hidden="true">
            <span class="seg seg-non" id="segNon" style="flex:0 0 0%"></span>
            <span class="seg seg-vil" id="segVil" style="flex:0 0 0%"></span>
            <span class="seg seg-uni" id="segUni" style="flex:0 0 0%"></span>
            <span class="seg seg-pro" id="segPro" style="flex:0 0 0%"></span>
          </div>
          <div class="share-label"><strong id="shareN">0</strong> of {total:,} locations</div>
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
    const regionFilter = document.getElementById('regionFilter');
    const searchInput = document.getElementById('searchInput');
    const tbody = document.getElementById('tableBody');
    const emptyState = document.getElementById('emptyState');
    const shareN = document.getElementById('shareN');
    const segNon = document.getElementById('segNon');
    const segVil = document.getElementById('segVil');
    const segUni = document.getElementById('segUni');
    const segPro = document.getElementById('segPro');

    let activeCompetitor = '';

    // Region options from data
    const regions = Array.from(new Set(DATA.map((r) => r.region).filter(Boolean))).sort((a, b) => a.localeCompare(b));
    regions.forEach((region) => {{
      const opt = document.createElement('option');
      opt.value = region;
      opt.textContent = region;
      regionFilter.appendChild(opt);
    }});

    function rowClass(name) {{
      if (name === 'NON') return 'c-NON';
      if (name === 'Villbrygg') return 'c-Villbrygg';
      if (name === 'Unified Ferments') return 'c-Unified';
      if (name === 'Researched Prospect Locations') return 'c-Prospects';
      return '';
    }}

    function escapeHtml(s) {{
      return String(s)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }}

    function matchesRegionSearch(row) {{
      const region = regionFilter.value;
      const q = searchInput.value.trim().toLowerCase();
      if (region && row.region !== region) return false;
      if (!q) return true;
      const hay = [row.competitor, row.name, row.address, row.city, row.region, row.type]
        .join(' ').toLowerCase();
      return hay.includes(q);
    }}

    function filtered() {{
      return DATA.filter((row) => {{
        if (activeCompetitor && row.competitor !== activeCompetitor) return false;
        return matchesRegionSearch(row);
      }});
    }}

    function updateChipCounts() {{
      const base = DATA.filter(matchesRegionSearch);
      const counts = {{
        '': base.length,
        'NON': 0,
        'Villbrygg': 0,
        'Unified Ferments': 0,
        'Researched Prospect Locations': 0,
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
      }};
      rows.forEach((row) => {{
        if (brands[row.competitor] != null) brands[row.competitor] += 1;
      }});
      const pct = (v) => (n ? ((v / n) * 100) : 0);
      segNon.style.flex = '0 0 ' + pct(brands.NON) + '%';
      segVil.style.flex = '0 0 ' + pct(brands.Villbrygg) + '%';
      segUni.style.flex = '0 0 ' + pct(brands['Unified Ferments']) + '%';
      segPro.style.flex = '0 0 ' + pct(brands['Researched Prospect Locations']) + '%';
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
        const meta = [row.address, row.city, row.region, row.type]
          .filter(Boolean).map(escapeHtml).join(' · ');
        return `<tr class="${{rowClass(row.competitor)}}">
          <td class="col-brand">${{escapeHtml(row.competitor)}}</td>
          <td class="name-cell">${{escapeHtml(row.name)}}<span class="meta-line">${{meta}}</span></td>
          <td class="col-address">${{addr || '<span class="muted">—</span>'}}</td>
          <td class="col-city">${{escapeHtml(row.city)}}</td>
          <td class="col-region">${{escapeHtml(row.region)}}</td>
          <td class="col-type muted">${{escapeHtml(row.type)}}</td>
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
    regionFilter.addEventListener('change', render);
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
