"""Universal TRMNL template: pasted into each private plugin once.

The template loops over ``L`` (the Display's layout, ``[[code, x, y, w, h], …]``)
and ``G`` (``[cols, rows]``) sent in merge_variables, drawing each widget by
its short code on a CSS grid. Layout changes therefore need a push, never a
re-paste. Each widget snippet reads ``cw`` / ``ch`` (its cell span) to pick
a compact or roomy variant.
"""

from __future__ import annotations

import re

from stonepi_contracts import WIDGET_CATALOG

# Keys every push carries regardless of widgets.
# TB = title bar position ("top" | "bottom" | "off"; blank = bottom), TN = title text.
BASE_KEYS = ("L", "G", "TB", "TN", "updated_at")
TITLE_BAR_POSITIONS = ("bottom", "top", "off")

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _h12(dt) -> str:
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


# Title-bar date styles: id → (label for the picker, formatter). Portable (no %-d).
DATE_FORMATS = {
    "day_time": ("Sat 27 Sep, 14:30", lambda d: f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}, {d:%H:%M}"),
    "date_time": ("27 Sep 2026, 14:30", lambda d: f"{d.day} {_MONTHS[d.month - 1]} {d.year}, {d:%H:%M}"),
    "dmy": ("27/09/2026 14:30", lambda d: f"{d:%d/%m/%Y %H:%M}"),
    "mdy": ("09/27/2026 2:30 PM", lambda d: f"{d:%m/%d/%Y} {_h12(d)}"),
    "iso": ("2026-09-27 14:30", lambda d: f"{d:%Y-%m-%d %H:%M}"),
    "time": ("14:30", lambda d: f"{d:%H:%M}"),
}
DEFAULT_DATE_FORMAT = "day_time"


def format_updated(stamp: str | None, fmt: str | None) -> str:
    """Format an ISO timestamp in the host's local time with a DATE_FORMATS style."""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(str(stamp)).astimezone()
    except (TypeError, ValueError):
        return ""
    _, fn = DATE_FORMATS.get(str(fmt or ""), DATE_FORMATS[DEFAULT_DATE_FORMAT])
    return fn(dt)

_STATUS_PILL = """\
{% if system_ok %}<span class="label label--small label--filled">OK</span>\
{% else %}<span class="label label--small label--outline">Check</span>{% endif %}"""

WIDGET_SNIPPETS: dict[str, str] = {
    "sys": """\
<div class="flex flex--row flex--between flex--center mb--1">
  <span class="title title--small">{{ hostname }}</span>
  """ + _STATUS_PILL + """
</div>
<div class="grid {% if cw >= 4 %}grid--cols-5{% else %}grid--cols-4{% endif %} gap">
  <div class="flex flex--col flex--center"><span class="label label--small">CPU</span><span class="value {% if ch >= 2 %}value--xlarge{% else %}value--small{% endif %} value--tnums">{{ cpu_disp }}</span></div>
  <div class="flex flex--col flex--center"><span class="label label--small">RAM</span><span class="value {% if ch >= 2 %}value--xlarge{% else %}value--small{% endif %} value--tnums">{{ mem_disp }}</span></div>
  {% if cw >= 4 %}<div class="flex flex--col flex--center"><span class="label label--small">Disk</span><span class="value value--small value--tnums">{{ disk_disp }}</span></div>{% endif %}
  <div class="flex flex--col flex--center"><span class="label label--small">Temp</span><span class="value {% if ch >= 2 %}value--xlarge{% else %}value--small{% endif %} value--tnums">{{ temp_disp }}</span></div>
  <div class="flex flex--col flex--center"><span class="label label--small">Up</span><span class="value value--small">{{ uptime }}</span></div>
</div>""",
    "wat": """\
<span class="title title--small">Watch</span>
<div class="mt--1">
{% if watch_level == "healthy" %}<span class="label label--filled">Healthy</span>\
{% elsif watch_level == "critical" %}<span class="label label--filled label--inverted">Critical</span>\
{% else %}<span class="label label--outline">Attention</span>{% endif %}
</div>
<span class="description mt--1">{{ watch_summary }}</span>
{% if ch >= 2 %}{% for a in alerts limit: 4 %}<span class="description mt--1">· {{ a.message }}</span>{% endfor %}{% endif %}""",
    "app": """\
<div class="flex flex--row flex--between flex--center">
  <span class="title title--small">Apps</span>
  <span class="label label--small">{{ apps_up }}/{{ apps_total }} up</span>
</div>
{% if ch >= 2 %}{% assign n = 8 %}{% else %}{% assign n = 3 %}{% endif %}
{% for app in apps limit: n %}
<div class="flex flex--row flex--between flex--center mt--1">
  <span class="description">{{ app.n }}</span>
  {% if app.running %}<span class="label label--small">Up</span>{% else %}<span class="label label--small label--filled">Down</span>{% endif %}
</div>
{% endfor %}""",
    "sto": """\
<span class="title title--small">Storage</span>
<div class="{% if cw >= 2 %}grid grid--cols-2 gap{% else %}flex flex--col{% endif %} mt--1">
  <div class="flex flex--col"><span class="label label--small">Disk</span><span class="value value--small value--tnums">{{ disk_disp }}</span></div>
  <div class="flex flex--col"><span class="label label--small">Backup</span><span class="value value--small">{{ backup_when }}</span>
  {% unless backup_ok %}<div><span class="label label--small label--filled">Check</span></div>{% endunless %}</div>
</div>""",
    "nws": """\
<span class="title title--small">News</span>
<span class="value {% if ch >= 2 %}value--xlarge{% else %}value--small{% endif %} value--tnums mt--1">{{ nc_feeds }} feeds</span>
<span class="label label--small">Updated {{ nc_updated }}</span>""",
    "evt": """\
<span class="title title--small">Next event</span>
<span class="description mt--1">{% if et_next == "None" %}Nothing coming up{% else %}{{ et_next }}{% endif %}</span>""",
    "fil": """\
<div class="flex flex--row flex--between flex--center">
  <span class="title title--small">Files</span>
  {% if fs_ok %}<span class="label label--small">Up</span>{% else %}<span class="label label--small label--filled">Down</span>{% endif %}
</div>
<span class="value {% if ch >= 2 %}value--xlarge{% else %}value--small{% endif %} value--tnums mt--1">{{ fs_pages }} pages</span>""",
    "pin": """\
<span class="title title--small">Pinboard</span>
{% if ch >= 2 %}{% assign n = 5 %}{% else %}{% assign n = 2 %}{% endif %}
{% for line in pinboard_lines limit: n %}<span class="description mt--1">{{ line }}</span>{% endfor %}
{% if pinboard_more > 0 %}<div><span class="label label--small label--outline">+{{ pinboard_more }} more</span></div>{% endif %}""",
    "prw": """\
<span class="title title--small">PriceWatch</span>
<span class="description mt--1">{{ pw_detail }}</span>""",
    "spt": """\
<span class="title title--small">Next matches</span>
<span class="description mt--1">{{ sg_detail }}</span>""",
}

TITLE_BAR = """\
<div class="title_bar">
  <img class="image" src="https://trmnl.com/images/plugins/trmnl--render.svg" />
  <span class="title">{{ TN | default: "StonePi" }}</span>
  <span class="instance">{{ updated_at }}</span>
</div>"""


def _build_universal_template() -> str:
    cases = []
    for desc in WIDGET_CATALOG:
        snippet = WIDGET_SNIPPETS.get(desc.code)
        if snippet:
            cases.append(f'  {{% when "{desc.code}" %}}\n{snippet}')
    return (
        "{% comment %}StonePi universal Display template — paste once; layout comes from the push.{% endcomment %}\n"
        '{% if TB == "top" %}' + TITLE_BAR + "{% endif %}\n"
        '<div class="layout" style="display:grid;'
        "grid-template-columns:repeat({{ G[0] }},minmax(0,1fr));"
        "grid-template-rows:repeat({{ G[1] }},minmax(0,1fr));"
        'gap:8px;align-items:stretch;justify-items:stretch;width:100%;height:100%">\n'
        "{% for w in L %}{% assign cw = w[3] %}{% assign ch = w[4] %}\n"
        '<div class="outline rounded p--8 flex flex--col flex--stretch gap--xsmall" style="grid-column:{{ w[1] }} / span {{ cw }};'
        'grid-row:{{ w[2] }} / span {{ ch }};overflow:hidden;min-width:0;min-height:0">\n'
        "{% case w[0] %}\n" + "\n".join(cases) + "\n{% endcase %}\n</div>\n"
        "{% else %}\n"
        '<div class="flex flex--col flex--center" style="grid-column:1 / -1;grid-row:1 / -1">'
        '<span class="title">No widgets yet</span>'
        '<span class="description">Add widgets to this Display in StonePi Notify.</span></div>\n'
        "{% endfor %}\n</div>\n"
        '{% unless TB == "top" or TB == "off" %}' + TITLE_BAR + "{% endunless %}\n"
    )


UNIVERSAL_TEMPLATE = _build_universal_template()

# --- variable analysis -----------------------------------------------------

_OUTPUT = re.compile(r"\{\{-?\s*(.+?)\s*-?\}\}")
_TAG = re.compile(r"\{%-?\s*(\w+)\s+(.*?)\s*-?%\}")
_PATH = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_KEYWORDS = {"blank", "empty", "nil", "true", "false", "and", "or", "contains", "limit", "in", "offset", "reversed"}


def liquid_references(markup: str) -> tuple[set[str], dict[str, str]]:
    """Return (dotted variable paths read, loop variable → collection) for a Liquid snippet.

    Deliberately small: covers the tags these templates use (output, if/elsif/
    unless/case/when, for, assign).
    """
    loops: dict[str, str] = {}
    assigned: set[str] = set()
    exprs: list[str] = []
    for tag, rest in _TAG.findall(markup):
        if tag == "for":
            var, _, coll = rest.partition(" in ")
            coll_name = coll.split()[0]
            loops[var.strip()] = coll_name
            exprs.append(coll_name)
            exprs.append(coll[len(coll_name):])
        elif tag == "assign":
            name, _, value = rest.partition("=")
            assigned.add(name.strip())
            exprs.append(value)
        elif tag in {"if", "elsif", "unless", "case", "when"}:
            exprs.append(rest)
    exprs.extend(expr.split("|")[0] for expr in _OUTPUT.findall(markup))
    paths: set[str] = set()
    for expr in exprs:
        cleaned = re.sub(r"\"[^\"]*\"|'[^']*'|\[\d+\]|\b\d+\b", " ", expr)
        for path in _PATH.findall(cleaned):
            head = path.split(".")[0]
            if head not in _KEYWORDS and head not in assigned:
                paths.add(path)
    return paths, loops


def snippet_keys(code: str) -> set[str]:
    """Top-level merge_variables keys a widget snippet reads."""
    paths, loops = liquid_references(WIDGET_SNIPPETS.get(code, ""))
    keys = set()
    for path in paths:
        head = path.split(".")[0]
        keys.add(loops.get(head, head))
    return keys - {"w", "cw", "ch"}
