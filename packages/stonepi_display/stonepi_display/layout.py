from __future__ import annotations

DEFAULT_INTERVAL_MIN = 30
MIN_INTERVAL_MIN = 10
MAX_INTERVAL_MIN = 120
DEFAULT_DEVICE = "og"
DEFAULT_DESIGN = "household"

BLOCK_CATALOG = (
    {"id": "system", "label": "System", "blurb": "Hostname, status, CPU, RAM, temp, uptime"},
    {"id": "watch", "label": "Watch", "blurb": "Healthy / attention / critical rollup"},
    {"id": "apps", "label": "Apps", "blurb": "Installed / running pills"},
    {"id": "newscast", "label": "NewsCast", "blurb": "Feed count and last update"},
    {"id": "eventtrakr", "label": "EventTrakr", "blurb": "Next favourite event"},
    {"id": "fileserve", "label": "FileServe", "blurb": "Hosted page count"},
    {"id": "pinboard", "label": "Pinboard", "blurb": "Notices and reminders"},
    {"id": "storage", "label": "Storage", "blurb": "Disk used and last backup"},
)
BLOCK_IDS = {item["id"] for item in BLOCK_CATALOG}

DEVICE_PROFILES = (
    {
        "id": "og",
        "label": 'TRMNL 7.5" (OG)',
        "blurb": "800×480 landscape — compact for classic OG. Tuned for B/W (1-bit); same markup works on 2-bit / 4-bit / colour via TRMNL Framework.",
        "width": 800,
        "height": 480,
        "density": "compact",
        "screen_class": "screen--og",
        "bit_depth": "1bit",
    },
    {
        "id": "v2",
        "label": "TRMNL V2 (larger)",
        "blurb": "1040×780 — roomier type. Same Liquid markup; Framework maps labels/outlines to the panel’s palette (gray or colour).",
        "width": 1040,
        "height": 780,
        "density": "roomy",
        "screen_class": "screen--v2",
        "bit_depth": "4bit",
    },
)
DEVICE_IDS = {item["id"] for item in DEVICE_PROFILES}
DEVICE_BY_ID = {item["id"]: item for item in DEVICE_PROFILES}

DESIGN_PRESETS = (
    {
        "id": "household",
        "label": "Household focus",
        "blurb": "Watch + Apps first; product cards (2×2 on 7.5\" OG, 4-across on V2); thin CPU/RAM/Disk/Backup strip. Follows TRMNL Framework screen sizes.",
        "fixed": True,
    },
    {
        "id": "status",
        "label": "Status wall",
        "blurb": "Ops board: hostname + SYSTEM, five-metric strip, Services list (wide), Alerts + Storage & Backup. Fixed composition.",
        "fixed": True,
    },
    {
        "id": "custom",
        "label": "Custom",
        "blurb": "Freeform drag-and-drop of any blocks.",
        "fixed": False,
    },
)
DESIGN_IDS = {item["id"] for item in DESIGN_PRESETS}
DESIGN_BY_ID = {item["id"]: item for item in DESIGN_PRESETS}


def normalize_device(raw) -> str:
    value = str(raw or "").strip().lower()
    return value if value in DEVICE_IDS else DEFAULT_DEVICE


def normalize_design(raw) -> str:
    value = str(raw or "").strip().lower()
    return value if value in DESIGN_IDS else DEFAULT_DESIGN


def device_profile(device: str | None = None) -> dict:
    return DEVICE_BY_ID[normalize_device(device)]


def design_profile(design: str | None = None) -> dict:
    return DESIGN_BY_ID[normalize_design(design)]


def _product_card(*, badge: str, title: str, body: str, compact: bool = False) -> str:
    """Product cell with a letter badge. Prefer filled badges — strongest on 1-bit B/W."""
    if compact:
        return f"""\
    <div class="outline rounded p-2">
      <div class="flex flex--row flex--between flex--center mb--1">
        <span class="label label--filled label--small">{badge}</span>
        <span class="label label--small">{title}</span>
      </div>
{body}
    </div>"""
    return f"""\
    <div class="outline rounded p-2">
      <div class="flex flex--row flex--center gap--small mb--1">
        <span class="label label--filled label--small">{badge}</span>
        <span class="title title--small">{title}</span>
      </div>
{body}
    </div>"""


def _snippets(*, density: str) -> dict[str, str]:
    compact = density == "compact"
    pad = "p-2" if compact else "p-4"
    title = "title title--small" if compact else "title"
    metric = "value value--large value--tnums" if compact else "value value--xxlarge value--tnums"
    metric_mid = "value value--small value--tnums" if compact else "value value--xlarge value--tnums"
    desc = "description" if compact else "description description--large"
    app_gap = "mt--1" if compact else "mt--2"
    # OG 7.5" half-column can't fit 7 long names + Running pills.
    app_limit = 4 if compact else 8
    watch_label = "label label--error label--filled label--small" if compact else "label label--error label--filled label--large"
    watch_ok = "label label--success label--filled label--small" if compact else "label label--success label--filled label--large"
    watch_attn = "label label--error label--outline label--small" if compact else "label label--error label--outline label--large"
    # Short names fit 2×2 cells on 800px; roomy keeps full product names.
    nc_title = "News" if compact else "NewsCast"
    et_title = "Events" if compact else "EventTrakr"
    fs_title = "Files" if compact else "FileServe"
    pb_title = "Pins" if compact else "Pinboard"
    return {
        "system": f"""\
  <div class="outline rounded {pad}">
    <div class="flex flex--row flex--between flex--center mb--1">
      <div>
        <span class="{title}">{{{{ hostname }}}}</span>
        <span class="description">System</span>
      </div>
      {{% if system_ok %}}
      <span class="label label--success label--filled">Operational</span>
      {{% else %}}
      <span class="label label--error label--filled">Attention</span>
      {{% endif %}}
    </div>
    <div class="grid grid--cols-4 gap">
      <div class="flex flex--col flex--center">
        <span class="label label--small">CPU</span>
        <span class="{metric}">{{{{ cpu_disp }}}}</span>
      </div>
      <div class="flex flex--col flex--center">
        <span class="label label--small">RAM</span>
        <span class="{metric}">{{{{ mem_disp }}}}</span>
      </div>
      <div class="flex flex--col flex--center">
        <span class="label label--small">Temp</span>
        <span class="{metric}">{{{{ temp_disp }}}}</span>
      </div>
      <div class="flex flex--col flex--center">
        <span class="label label--small">Uptime</span>
        <span class="value value--small">{{{{ uptime }}}}</span>
      </div>
    </div>
  </div>""",
        "watch": f"""\
  <div class="outline rounded {pad}">
    <span class="title title--small">Watch</span>
    {{% if watch_level == "healthy" %}}
    <span class="{watch_ok}">Healthy</span>
    {{% elsif watch_level == "critical" %}}
    <span class="{watch_label}">Critical</span>
    {{% else %}}
    <span class="{watch_attn}">Attention</span>
    {{% endif %}}
    <span class="{desc} mt--1">{{{{ watch_summary }}}}</span>
  </div>""",
        "apps": f"""\
  <div class="outline rounded {pad}">
    <span class="title title--small">Apps</span>
    {{% for app in apps limit:{app_limit} %}}
    <div class="flex flex--row flex--between flex--center {app_gap}">
      <span class="{desc}">{{{{ app.n }}}}</span>
      {{% if app.running %}}
      <span class="label label--success label--filled label--small">Up</span>
      {{% else %}}
      <span class="label label--error label--filled label--small">Down</span>
      {{% endif %}}
    </div>
    {{% endfor %}}
  </div>""",
        "newscast": _product_card(
            badge="NC",
            title=nc_title,
            compact=compact,
            body=f"""\
      <span class="{metric_mid}">{{{{ nc_feeds }}}} feeds</span>
      <span class="label label--small">{{{{ nc_updated }}}}</span>""",
        ),
        "eventtrakr": _product_card(
            badge="ET",
            title=et_title,
            compact=compact,
            body=f"""\
      <span class="label label--small">Next</span>
      <span class="{desc}">{{{{ et_next }}}}</span>""",
        ),
        "fileserve": _product_card(
            badge="FS",
            title=fs_title,
            compact=compact,
            body=f"""\
      <div class="flex flex--row flex--between flex--center">
        <span class="{metric_mid}">{{{{ fs_pages }}}} pages</span>
        {{% if fs_ok %}}
        <span class="label label--success label--filled label--small">Up</span>
        {{% else %}}
        <span class="label label--error label--filled label--small">Down</span>
        {{% endif %}}
      </div>""",
        ),
        "pinboard": _product_card(
            badge="PB",
            title=pb_title,
            compact=compact,
            body=f"""\
      {{% for line in pinboard_lines limit:1 %}}
      <span class="{desc}">{{{{ line }}}}</span>
      {{% endfor %}}
      {{% if pinboard_more %}}
      <span class="label label--outline label--small">+{{{{ pinboard_more }}}}</span>
      {{% endif %}}""",
        ),
        "storage": f"""\
  <div class="outline rounded {pad}">
    <span class="title title--small">Storage &amp; Backup</span>
    <div class="grid grid--cols-2 gap mt--1">
      <div class="flex flex--col">
        <span class="label label--small">Disk</span>
        <span class="{metric_mid}">{{{{ disk_disp }}}}</span>
      </div>
      <div class="flex flex--col">
        <span class="label label--small">Backup</span>
        <span class="value value--small">{{{{ backup_when }}}}</span>
        {{% if backup_ok %}}
        <span class="label label--success label--filled label--small">OK</span>
        {{% else %}}
        <span class="label label--error label--filled label--small">Check</span>
        {{% endif %}}
      </div>
    </div>
  </div>""",
    }


BLOCK_SNIPPETS = _snippets(density="compact")
BLOCK_SNIPPETS_ROOMY = _snippets(density="roomy")

TITLE_BAR_MARKUP = """\
<div class="title_bar">
  <img class="image" src="https://trmnl.com/images/plugins/trmnl--render.svg" />
  <span class="title">StonePi</span>
  <span class="instance">{{ updated_at }}</span>
</div>
"""


def default_layout(device: str | None = None, design: str | None = None) -> dict:
    device_id = normalize_device(device)
    design_id = normalize_design(design)
    if design_id == "household":
        items = [
            {"id": "watch", "span": 1},
            {"id": "apps", "span": 1},
            {"id": "newscast", "span": 1},
            {"id": "eventtrakr", "span": 1},
            {"id": "fileserve", "span": 1},
            {"id": "pinboard", "span": 1},
        ]
    elif design_id == "status":
        items = [
            {"id": "system", "span": 2},
            {"id": "apps", "span": 2},
            {"id": "watch", "span": 1},
            {"id": "storage", "span": 1},
        ]
    elif device_id == "v2":
        items = [
            {"id": "system", "span": 2},
            {"id": "watch", "span": 1},
            {"id": "apps", "span": 1},
            {"id": "newscast", "span": 1},
            {"id": "eventtrakr", "span": 1},
            {"id": "fileserve", "span": 1},
            {"id": "pinboard", "span": 1},
        ]
    else:
        items = [
            {"id": "watch", "span": 1},
            {"id": "apps", "span": 1},
            {"id": "newscast", "span": 1},
            {"id": "eventtrakr", "span": 1},
            {"id": "fileserve", "span": 1},
            {"id": "pinboard", "span": 1},
        ]
    return {"cols": 2, "items": items, "device": device_id, "design": design_id}


def normalize_layout(raw, *, device: str | None = None, design: str | None = None) -> dict:
    raw_device = (raw or {}).get("device") if isinstance(raw, dict) else None
    raw_design = (raw or {}).get("design") if isinstance(raw, dict) else None
    device_id = normalize_device(device if device is not None else raw_device)
    design_id = normalize_design(design if design is not None else raw_design)
    base = default_layout(device_id, design_id)
    if not isinstance(raw, dict):
        return base
    if design_id in {"household", "status"}:
        # Fixed composition — keep recommended items for this design.
        return base
    items_in = raw.get("items")
    if not isinstance(items_in, list):
        return base
    seen: set[str] = set()
    items: list[dict] = []
    for entry in items_in:
        if not isinstance(entry, dict):
            continue
        block_id = str(entry.get("id") or "").strip()
        if block_id not in BLOCK_IDS or block_id in seen:
            continue
        try:
            span = int(entry.get("span") or 1)
        except (TypeError, ValueError):
            span = 1
        span = 2 if span >= 2 else 1
        seen.add(block_id)
        items.append({"id": block_id, "span": span})
    return {"cols": 2, "items": items, "device": device_id, "design": design_id}


def snippets_for(device: str | None = None) -> dict[str, str]:
    profile = device_profile(device)
    if profile["density"] == "roomy":
        return dict(BLOCK_SNIPPETS_ROOMY)
    return dict(BLOCK_SNIPPETS)


def _build_household_markup(*, device: str) -> str:
    """Watch + Apps focus; product cards; thin system strip. OG uses 2×2 products."""
    profile = device_profile(device)
    snips = snippets_for(device)
    # 4-across clips on 800×480; 2×2 is the OG-safe layout. V2 keeps a single row.
    product_cols = "grid--cols-4" if profile["density"] == "roomy" else "grid--cols-2"
    parts = [
        '<div class="layout layout--col gap">',
        '  <div class="grid grid--cols-2 gap">',
        snips["watch"],
        snips["apps"],
        "  </div>",
        f'  <div class="grid {product_cols} gap">',
        snips["newscast"],
        snips["eventtrakr"],
        snips["fileserve"],
        snips["pinboard"],
        "  </div>",
        '  <div class="outline rounded p-2">',
        '    <div class="grid grid--cols-4 gap">',
        '      <div class="flex flex--col flex--center"><span class="label label--small">CPU</span><span class="value value--small value--tnums">{{ cpu_disp }}</span></div>',
        '      <div class="flex flex--col flex--center"><span class="label label--small">RAM</span><span class="value value--small value--tnums">{{ mem_disp }}</span></div>',
        '      <div class="flex flex--col flex--center"><span class="label label--small">Disk</span><span class="value value--small value--tnums">{{ disk_disp }}</span></div>',
        '      <div class="flex flex--col flex--center"><span class="label label--small">Backup</span><span class="value value--small">{{ backup_when }}</span></div>',
        "    </div>",
        "  </div>",
        "</div>",
        TITLE_BAR_MARKUP.rstrip(),
    ]
    return "\n".join(parts) + "\n"


def _build_status_markup(*, device: str) -> str:
    """Ops board: header + 5 metrics + Services (2/3) + Alerts/Storage (1/3)."""
    compact = device_profile(device)["density"] == "compact"
    pad = "p-2" if compact else "p-4"
    title = "title title--small" if compact else "title"
    metric = "value value--large value--tnums" if compact else "value value--xlarge value--tnums"
    metric_mid = "value value--small value--tnums" if compact else "value value--large value--tnums"
    desc = "description" if compact else "description description--large"
    app_gap = "mt--1" if compact else "mt--2"
    app_limit = 6 if compact else 8
    return f"""\
<div class="layout layout--col gap">
  <div class="flex flex--row flex--between flex--center">
    <div class="flex flex--row flex--center gap--small">
      <span class="title">{{{{ hostname }}}}</span>
      <span class="label label--small">SYSTEM</span>
    </div>
    <span class="label label--outline label--small">Refreshed {{{{ refreshed_ago }}}}</span>
  </div>
  <div class="grid grid--cols-5 gap">
    <div class="flex flex--col flex--center">
      <span class="label label--small">CPU</span>
      <span class="{metric}">{{{{ cpu_disp }}}}</span>
    </div>
    <div class="flex flex--col flex--center">
      <span class="label label--small">RAM</span>
      <span class="{metric}">{{{{ mem_disp }}}}</span>
    </div>
    <div class="flex flex--col flex--center">
      <span class="label label--small">DISK</span>
      <span class="{metric}">{{{{ disk_disp }}}}</span>
    </div>
    <div class="flex flex--col flex--center">
      <span class="label label--small">TEMP</span>
      <span class="{metric}">{{{{ temp_disp }}}}</span>
    </div>
    <div class="flex flex--col flex--center">
      <span class="label label--small">UPTIME</span>
      <span class="{metric_mid}">{{{{ uptime }}}}</span>
    </div>
  </div>
  <div class="grid grid--cols-3 gap">
    <div class="col col--span-2 outline rounded {pad}">
      <div class="flex flex--row flex--between flex--center mb--1">
        <span class="{title}">Services</span>
        <span class="label label--small">{{{{ apps_up }}}} of {{{{ apps_total }}}} up</span>
      </div>
      {{% for app in apps limit:{app_limit} %}}
      <div class="flex flex--row flex--between flex--center {app_gap}">
        <div class="flex flex--row flex--center gap--small">
          <span class="{title}">{{{{ app.n }}}}</span>
          <span class="{desc}">{{{{ app.d }}}}</span>
        </div>
        <div class="flex flex--row flex--center gap--small">
          {{% if app.m != blank %}}
          <span class="label label--small">{{{{ app.m }}}}</span>
          {{% endif %}}
          {{% if app.running %}}
          <span class="label label--outline label--small">UP</span>
          {{% else %}}
          <span class="label label--error label--filled label--small">DOWN</span>
          {{% endif %}}
        </div>
      </div>
      {{% endfor %}}
    </div>
    <div class="col col--span-1">
      <div class="flex flex--col gap">
        <div class="outline rounded {pad}">
          <span class="{title}">Alerts</span>
          {{% if watch_level == "healthy" %}}
          <div class="flex flex--row flex--center gap--small {app_gap}">
            <span class="label label--success label--filled label--small">OK</span>
            <span class="{desc}">{{{{ watch_summary }}}}</span>
          </div>
          {{% elsif watch_level == "critical" %}}
          <div class="flex flex--row flex--center gap--small {app_gap}">
            <span class="label label--error label--filled label--small">CRITICAL</span>
            <span class="{desc}">{{{{ watch_summary }}}}</span>
          </div>
          {{% else %}}
          <div class="flex flex--row flex--center gap--small {app_gap}">
            <span class="label label--error label--outline label--small">ATTENTION</span>
            <span class="{desc}">{{{{ watch_summary }}}}</span>
          </div>
          {{% endif %}}
        </div>
        <div class="outline rounded {pad}">
          <span class="{title}">Storage &amp; Backup</span>
          <div class="grid grid--cols-2 gap {app_gap}">
            <div class="flex flex--col">
              <span class="label label--small">Disk used</span>
              <span class="{metric_mid}">{{{{ disk_disp }}}}</span>
            </div>
            <div class="flex flex--col">
              <span class="label label--small">Last backup</span>
              <div class="flex flex--row flex--center gap--small">
                <span class="value value--small">{{{{ backup_when }}}}</span>
                {{% if backup_ok %}}
                <span class="label label--success label--filled label--small">OK</span>
                {{% else %}}
                <span class="label label--error label--filled label--small">FAILED</span>
                {{% endif %}}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
"""


def build_markup(
    layout: dict | None = None,
    *,
    device: str | None = None,
    design: str | None = None,
) -> str:
    layout = normalize_layout(
        layout if layout is not None else default_layout(device, design),
        device=device,
        design=design,
    )
    device_id = normalize_device(device or layout.get("device"))
    design_id = normalize_design(design or layout.get("design"))
    if design_id == "household":
        return _build_household_markup(device=device_id)
    if design_id == "status":
        return _build_status_markup(device=device_id)

    snippets = snippets_for(device_id)
    items = layout.get("items") or []
    parts: list[str] = ['<div class="layout layout--col gap">']
    i = 0
    while i < len(items):
        item = items[i]
        snippet = snippets.get(item["id"])
        if not snippet:
            i += 1
            continue
        if item["span"] >= 2:
            parts.append(snippet)
            i += 1
            continue
        pair = [snippet]
        if i + 1 < len(items) and items[i + 1]["span"] < 2:
            next_snip = snippets.get(items[i + 1]["id"])
            if next_snip:
                pair.append(next_snip)
                i += 2
            else:
                i += 1
        else:
            i += 1
        parts.append('  <div class="grid grid--cols-2 gap">')
        parts.extend(pair)
        parts.append("  </div>")
    parts.append("</div>")
    parts.append(TITLE_BAR_MARKUP.rstrip())
    return "\n".join(parts) + "\n"


STARTER_MARKUP = build_markup(default_layout(DEFAULT_DEVICE, DEFAULT_DESIGN), device=DEFAULT_DEVICE, design=DEFAULT_DESIGN)
