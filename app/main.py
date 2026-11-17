import html
import logging
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import urlencode

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import fasthtml.common as fh
from fasthtml.common import *
from fasthtml.svg import Path, Rect
from monsterui.all import *

from app.auth import groups_omitted, in_group, oauth, user_from_claims
from app.browser import Entry, list_dir, list_files, resolve_inside, root, search_files
from app.config import DEV_USER, HTTPS_ONLY, LOGOUT_URL, REDIRECT_URI, SESSION_SECRET
from app.db import last_downloads, record_download
from app.sync_job import last_sync_summary, list_logs, read_log, start_sync, sync_state

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("c1")


def user_auth(req, sess):
    if not sess.get("user"):
        return RedirectResponse("/login", status_code=303)


beforeware = Beforeware(
    user_auth,
    skip=[r"/login", r"/auth/.*", r"/unauthorized"],
)

app, rt = fast_app(
    hdrs=tuple(Theme.blue.headers()) + (
        Style("""
.file-table a { color: inherit; }
.file-table th a { font-weight: 600; text-decoration: none; }
.file-table td, .file-table th { vertical-align: middle; }
.log-panel { background: hsl(var(--muted) / 0.45); }
.log-line { white-space: pre-wrap; }
.date-caution {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  margin-left: 0.5rem;
  border-radius: 9999px;
  background: #fef3c7;
  color: #92400e;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.125rem 0.5rem;
  vertical-align: middle;
}
.dark .date-caution { background: #451a03; color: #fcd34d; }
.date-caution[hidden] { display: none; }
.new-files {
  display: inline-flex;
  align-items: center;
  margin-left: 0.5rem;
  border-radius: 9999px;
  background: #dbeafe;
  color: #1e40af;
  font-size: 0.75rem;
  font-weight: 600;
  line-height: 1.25;
  padding: 0.125rem 0.5rem;
  vertical-align: middle;
  white-space: nowrap;
}
.dark .new-files { background: #1e3a8a; color: #bfdbfe; }
.day-picker { display: flex; flex-direction: column; gap: 0.65rem; max-height: 20rem; overflow: auto; }
.day-picker label { display: flex; align-items: flex-start; gap: 0.6rem; font-size: 0.875rem; cursor: pointer; }
.day-picker label input.uk-checkbox {
  width: 1.25rem;
  height: 1.25rem;
  min-width: 1.25rem;
  flex: 0 0 1.25rem;
  margin: 0.1rem 0 0;
}
.day-picker label:has(input:disabled) { opacity: 0.55; cursor: default; }
#grab-download { position: absolute; width: 0; height: 0; border: 0; }
#download-modal.uk-open {
  display: flex !important;
  align-items: flex-start;
  justify-content: center;
  padding: 5rem 1rem 2rem;
}
#download-modal .uk-modal-dialog { width: min(32rem, calc(100vw - 2rem)); margin: 0; }
.theme-icon-sun { display: none; }
.dark .theme-icon-sun { display: block; }
.dark .theme-icon-moon { display: none; }
"""),
        Script("""
function c1StoredTheme() {
  try { return JSON.parse(localStorage.getItem("__FRANKEN__") || "{}"); }
  catch { return {}; }
}
function c1ApplyTheme(mode) {
  const dark = mode === "dark" || (mode !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  const button = document.getElementById("theme-toggle");
  if (button) {
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  }
}
function c1ToggleTheme() {
  const stored = c1StoredTheme();
  stored.mode = document.documentElement.classList.contains("dark") ? "light" : "dark";
  localStorage.setItem("__FRANKEN__", JSON.stringify(stored));
  c1ApplyTheme(stored.mode);
}
document.addEventListener("click", (event) => {
  if (event.target.closest("#theme-toggle")) c1ToggleTheme();
});
document.addEventListener("DOMContentLoaded", () => c1ApplyTheme(c1StoredTheme().mode || ""));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (!c1StoredTheme().mode) c1ApplyTheme("");
});
function c1LocalTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const text = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
  return { date, text };
}
function c1RefreshTimes(root) {
  const scope = root && root.querySelectorAll ? root : document;
  scope.querySelectorAll("time[datetime]").forEach((node) => {
    const formatted = c1LocalTime(node.dateTime);
    if (formatted) node.textContent = formatted.text;
  });
  scope.querySelectorAll("option[data-utc]").forEach((node) => {
    const formatted = c1LocalTime(node.dataset.utc);
    if (formatted) node.textContent = formatted.text;
  });
  scope.querySelectorAll("tr[data-name-md]").forEach((row) => {
    const badge = row.querySelector(".date-caution");
    const stamp = row.querySelector("time[datetime]");
    if (!badge || !stamp) return;
    const formatted = c1LocalTime(stamp.dateTime);
    if (!formatted) return;
    const md = row.dataset.nameMd;
    const month = Number(md.slice(0, 2));
    const day = Number(md.slice(2));
    const probe = new Date(2024, month - 1, day);
    const valid = month >= 1 && month <= 12 && probe.getMonth() === month - 1 && probe.getDate() === day;
    let title = "";
    if (!valid) {
      title = "The file name contains " + md + ", which is not a valid month and day.";
    } else if (formatted.date.getMonth() + 1 !== month || formatted.date.getDate() !== day) {
      const shown = new Intl.DateTimeFormat(undefined, { month: "long", day: "numeric", year: "numeric" }).format(formatted.date);
      const named = new Intl.DateTimeFormat(undefined, { month: "long", day: "numeric", timeZone: "UTC" }).format(new Date(Date.UTC(2024, month - 1, day)));
      title = "This file is dated " + shown + " in your time zone, but the name says " + named + " (" + md + ").";
    }
    if (!title) {
      badge.hidden = true;
      badge.removeAttribute("title");
      badge.removeAttribute("aria-label");
      return;
    }
    badge.hidden = false;
    badge.title = title;
    badge.setAttribute("aria-label", title);
  });
}
function c1ShowDownloadModal() {
  const modal = document.getElementById("download-modal");
  if (!modal || modal.dataset.opened) return;
  if (!window.UIkit || !UIkit.modal) return;
  modal.dataset.opened = "1";
  UIkit.modal(modal).show();
}
document.addEventListener("click", (event) => {
  const modal = document.getElementById("download-modal");
  if (!modal) return;
  const closer = event.target.closest(".uk-modal-close, [data-uk-close]");
  const backdrop = event.target === modal;
  if (!closer && !backdrop) return;
  if (window.UIkit && UIkit.modal) UIkit.modal(modal).hide();
  else modal.remove();
});
document.addEventListener("DOMContentLoaded", () => c1RefreshTimes(document));
document.addEventListener("htmx:afterSwap", () => {
  c1RefreshTimes(document);
  c1ShowDownloadModal();
});
"""),
    ),
    pico=False,
    before=beforeware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    sess_https_only=HTTPS_ONLY,
    max_age=12 * 60 * 60,
    title="C1 Imports",
    canonical=False,
    static_path="/app/static",
)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.router.routes = [
    route for route in app.router.routes if "{ext:static}" not in getattr(route, "path", "")
]


def nav_link(label, href, active):
    cls = "text-sm font-medium" if active else "text-sm text-muted-foreground"
    return A(label, href=href, cls=cls, aria_current="page" if active else None)


def theme_toggle():
    return Button(
        UkIcon("sun", height=16, width=16, cls="theme-icon-sun"),
        UkIcon("moon", height=16, width=16, cls="theme-icon-moon"),
        submit=False,
        id="theme-toggle",
        aria_label="Switch color theme",
        aria_pressed="false",
        title="Switch color theme",
        cls=(ButtonT.ghost, ButtonT.icon, ButtonT.sm),
    )


def logo():
    mark = Svg(
        Rect(width="32", height="32", rx="8", fill="hsl(var(--primary))"),
        Path(d="M16.4 11.2a5.6 5.6 0 1 0 .05 9.6", fill="none", stroke="#fff", stroke_width="2.4", stroke_linecap="round"),
        Path(d="M19.4 13.1 21.5 11.2v9.6", fill="none", stroke="#fff", stroke_width="2.4", stroke_linecap="round", stroke_linejoin="round"),
        viewBox="0 0 32 32",
        width="28",
        height="28",
        aria_hidden="true",
        cls="shrink-0",
    )
    return A(
        mark,
        Span("C1 Imports", cls="text-[15px] font-semibold tracking-tight"),
        href="/",
        aria_label="C1 Imports",
        cls="inline-flex items-center gap-2.5",
    )


def shell(sess, section, *content):
    user = sess.get("user") or {}
    label = user.get("name") or user.get("email") or "Signed in"
    return (
        Title("C1 Imports"),
        NavBar(
            nav_link("Files", "/", section == "files"),
            nav_link("Logs", "/logs", section == "logs"),
            theme_toggle(),
            Span(label, cls="hidden text-sm text-muted-foreground sm:inline"),
            A("Sign out", href="/logout", cls="text-sm"),
            brand=logo(),
            sticky=True,
            cls="border-b px-6 py-4",
            right_cls="items-center gap-6",
        ),
        Container(*content, cls="py-8"),
    )


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


_LOG_LINE = re.compile(r"^\[(?P<when>[^\]]+)\] \[(?P<level>[A-Z]+)\] (?P<msg>.*)$")
_LOG_STAMP = re.compile(r"sftp_sync_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.log$")
_NAME_DATE = re.compile(r"ertv(\d{2})(\d{2})\.(?:dat|rpt)$", re.IGNORECASE)
_SORTS = ("name", "date", "downloaded")


def parse_utc(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith(" UTC"):
        text = text[:-4].strip()
    text = text.replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif "+" not in text[10:]:
        text += "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def utc_time(stamp: datetime):
    iso = stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fallback = stamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return Time(fallback, datetime=iso, cls="c1-time")


def name_month_day(filename: str) -> str | None:
    match = _NAME_DATE.search(filename)
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}"


def normalize_sort(sort: str, order: str) -> tuple[str, str]:
    if sort not in _SORTS:
        sort = "date"
    if order not in {"asc", "desc"}:
        order = "desc"
    return sort, order


def order_entries(entries: list[Entry], sort: str, order: str, history: dict, show_path: bool) -> list[Entry]:
    reverse = order == "desc"

    def name_key(entry: Entry) -> str:
        return (entry.rel if show_path else entry.name).casefold()

    folders = [entry for entry in entries if entry.is_dir]
    files = [entry for entry in entries if not entry.is_dir]
    if sort == "date":
        folders.sort(key=lambda entry: entry.created, reverse=reverse)
        files.sort(key=lambda entry: entry.created, reverse=reverse)
        return folders + files
    if sort == "downloaded":
        folders.sort(key=name_key)

        def downloaded_at(entry: Entry) -> datetime | None:
            info = history.get(entry.rel)
            return parse_utc(info["downloaded_at"]) if info else None

        known = [entry for entry in files if downloaded_at(entry)]
        known_ids = {id(entry) for entry in known}
        unknown = [entry for entry in files if id(entry) not in known_ids]
        known.sort(key=downloaded_at, reverse=reverse)
        unknown.sort(key=name_key)
        return folders + known + unknown
    folders.sort(key=name_key, reverse=reverse)
    files.sort(key=name_key, reverse=reverse)
    return folders + files


def last_downloaded_file(files: list[Entry]) -> Entry | None:
    history = last_downloads([entry.rel for entry in files])
    downloaded = [entry for entry in files if entry.rel in history]
    if not downloaded:
        return None

    def rank(entry: Entry):
        stamp = parse_utc(history[entry.rel]["downloaded_at"])
        when = stamp or datetime.min.replace(tzinfo=timezone.utc)
        return (when, entry.created, entry.rel.casefold())

    return max(downloaded, key=rank)


def download_bits(info):
    if not info:
        return Span("—", cls="text-muted-foreground")
    stamp = parse_utc(info["downloaded_at"])
    when = utc_time(stamp) if stamp else Span(info["downloaded_at"])
    return Div(
        Span(info["downloaded_by"], cls="block"),
        Span(when, cls="block text-xs text-muted-foreground"),
    )


def breadcrumbs(rel: str):
    parts = [part for part in rel.split("/") if part]
    items = [A("Files", href="/", cls="hover:text-foreground")]
    built: list[str] = []
    for index, part in enumerate(parts):
        built.append(part)
        items.append(UkIcon("chevron-right", height=14, width=14, cls="text-muted-foreground"))
        href = "/?" + urlencode({"path": "/".join(built)})
        if index == len(parts) - 1:
            items.append(Span(part, cls="text-foreground"))
        else:
            items.append(A(part, href=href, cls="hover:text-foreground"))
    return Nav(*items, cls="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground", aria_label="Folder")


def new_files_badge(files: list[Entry], allowed: set[date], zone: timezone):
    recent = [entry for entry in files if entry.created.astimezone(zone).date() in allowed]
    count = len(undownloaded(recent))
    if not count:
        return None
    noun = "file" if count == 1 else "files"
    verb = "has" if count == 1 else "have"
    detail = f"{count} {noun} from the last 14 days {verb} not been downloaded"
    return Span(f"{count} new", cls="new-files", title=detail, aria_label=detail)


def named_link(icon, label, href):
    return A(
        UkIcon(icon, height=16, width=16, cls="shrink-0 text-muted-foreground"),
        Span(label, cls="truncate"),
        href=href,
        cls="inline-flex max-w-full items-center gap-2",
    )


def file_rows(
    entries: list[Entry],
    history: dict,
    show_path: bool = False,
    empty: str = "No files",
    sort: str = "date",
    order: str = "desc",
) -> list:
    if not entries:
        return [Tr(Td(
            P(empty, cls="py-10 text-center text-sm text-muted-foreground"),
            colspan="4",
        ))]
    now = datetime.now(timezone.utc)
    zone = local_zone()
    allowed = set(recent_dates(now, zone))
    rows = []
    for entry in entries:
        label = entry.rel if show_path else entry.name
        caution = None
        badge = None
        row = {}
        if entry.is_dir:
            folder_params = {"path": entry.rel}
            if not (sort == "date" and order == "desc"):
                folder_params["sort"] = sort
                folder_params["order"] = order
            name = named_link("folder", label, "/?" + urlencode(folder_params))
            files = list_files(entry.rel)
            count = len(files)
            size = f"{count} file" if count == 1 else f"{count} files"
            latest = last_downloaded_file(files)
            downloaded = utc_time(latest.created) if latest else Span("—", cls="text-muted-foreground")
            badge = new_files_badge(files, allowed, zone)
        else:
            name = named_link("file", label, "/download?" + urlencode({"path": entry.rel}))
            size = human_size(entry.size or 0)
            downloaded = download_bits(history.get(entry.rel))
            month_day = name_month_day(entry.name)
            if month_day:
                row["data_name_md"] = month_day
                caution = Span(
                    UkIcon("triangle-alert", height=14, width=14),
                    "Check date",
                    cls="date-caution",
                    hidden=True,
                )
        name_cell = Td(name, *[item for item in (badge, caution) if item is not None])
        rows.append(Tr(
            name_cell,
            Td(size, cls="whitespace-nowrap text-sm text-muted-foreground"),
            Td(utc_time(entry.created), cls="whitespace-nowrap text-sm text-muted-foreground"),
            Td(downloaded),
            **row,
        ))
    return rows


def sort_link(column: str, label: str, sort: str, order: str, path: str, query: str):
    if sort == column:
        nxt = "asc" if order == "desc" else "desc"
        mark = " ↑" if order == "asc" else " ↓"
        aria = "ascending" if order == "asc" else "descending"
    else:
        nxt = "desc" if column in {"date", "downloaded"} else "asc"
        mark = ""
        aria = "none"
    params = {"sort": column, "order": nxt}
    if path:
        params["path"] = path
    if query:
        params["q"] = query
    href = "/?" + urlencode(params)
    return Th(
        A(
            label + mark,
            href=href,
            hx_get=href,
            hx_target="#file-list",
            hx_swap="outerHTML",
            hx_push_url="true",
        ),
        aria_sort=aria,
    )


def file_table(
    entries: list[Entry],
    caption: str = "",
    show_path: bool = False,
    empty: str = "No files",
    sort: str = "date",
    order: str = "desc",
    path: str = "",
    query: str = "",
    sync_sort_fields: bool = False,
):
    files = [entry.rel for entry in entries if not entry.is_dir]
    history = last_downloads(files)
    ordered = order_entries(entries, sort, order, history, show_path)
    head = P(caption, cls="border-b px-4 py-3 text-sm text-muted-foreground") if caption else None
    fields = (
        Input(type="hidden", name="sort", value=sort, id="sort-field", hx_swap_oob="true"),
        Input(type="hidden", name="order", value=order, id="order-field", hx_swap_oob="true"),
    ) if sync_sort_fields else ()
    return Div(
        head,
        *fields,
        Div(
            Table(
                Thead(Tr(
                    sort_link("name", "Name", sort, order, path, query),
                    Th("Size"),
                    sort_link("date", "Date", sort, order, path, query),
                    sort_link("downloaded", "Last downloaded", sort, order, path, query),
                )),
                Tbody(*file_rows(ordered, history, show_path=show_path, empty=empty, sort=sort, order=order)),
            ),
            cls="file-table overflow-x-auto",
        ),
        id="file-list",
        cls="overflow-hidden rounded-xl border bg-card",
    )


def pretty_last_run(summary: str) -> tuple[datetime | None, str]:
    match = _LOG_LINE.match(summary.strip())
    if not match:
        return None, summary
    message = match["msg"]
    if "completed in" in message:
        message = message.replace("SFTP sync process completed in", "Completed in")
    elif "already in progress" in message:
        message = "A sync was already running"
    elif message == "SFTP sync completed successfully":
        message = "Sync finished"
    return parse_utc(match["when"]), message


def sync_panel():
    running, pid = sync_state()
    when, detail = pretty_last_run(last_sync_summary())
    when_label = utc_time(when) if when else None
    status = f"Running · {pid}" if running and pid else ("Running" if running else "Idle")
    pill = "inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs font-medium "
    pill += "bg-primary/10 text-primary" if running else "bg-muted text-muted-foreground"
    button = {
        "submit": False,
        "cls": (ButtonT.primary, ButtonT.sm),
        "hx_post": "/sync/run",
        "hx_target": "#sync-status",
        "hx_swap": "outerHTML",
        "hx_confirm": "Start a sync now?",
    }
    if running:
        button["disabled"] = "disabled"
    return Div(
        Div(
            Span(Span(cls="h-1.5 w-1.5 rounded-full bg-current"), status, cls=pill),
            P(when_label, cls="mt-1 text-xs text-muted-foreground") if when_label else None,
        ),
        P(detail, cls="min-w-0 flex-1 truncate text-sm text-muted-foreground", title=detail),
        Button(UkIcon("refresh-cw", height=15, width=15), "Run sync", **button),
        id="sync-status",
        hx_get="/sync/status",
        hx_trigger="every 2s",
        hx_swap="outerHTML",
        cls="flex w-full flex-col gap-3 rounded-xl border bg-card px-4 py-3 sm:flex-row sm:items-center lg:max-w-xl",
    )


def search_box(path: str, query: str, sort: str, order: str):
    return Form(
        Input(
            type="search",
            name="q",
            value=query,
            id="q-field",
            placeholder="Search file names",
            hx_get="/",
            hx_trigger="keyup changed delay:300ms, search",
            hx_target="#file-list",
            hx_swap="outerHTML",
            hx_include="#path-field,#sort-field,#order-field",
        ),
        Input(type="hidden", name="path", value=path, id="path-field"),
        Input(type="hidden", name="sort", value=sort, id="sort-field"),
        Input(type="hidden", name="order", value=order, id="order-field"),
        cls="w-full sm:w-72",
    )


def page_heading(title, subtitle):
    return Div(
        H1(title, cls="text-2xl font-semibold tracking-tight"),
        P(subtitle, cls="text-sm text-muted-foreground"),
        cls="space-y-1",
    )


_GRAB_DAYS = 14
_GRAB_TZ = "js:{tz: String(new Date().getTimezoneOffset())}"


def local_zone() -> timezone:
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return timezone(offset)


def client_zone(tz: str) -> timezone:
    try:
        minutes = int(str(tz).strip() or "0")
    except ValueError:
        minutes = 0
    minutes = max(-16 * 60, min(16 * 60, minutes))
    return timezone(timedelta(minutes=-minutes))


def recent_dates(now: datetime, zone: timezone) -> list[date]:
    today = now.astimezone(zone).date()
    return [today - timedelta(days=offset) for offset in range(_GRAB_DAYS)]


def files_in_window(folder: str, now: datetime, zone: timezone) -> list[Entry]:
    allowed = set(recent_dates(now, zone))
    return [
        entry
        for entry in list_files(folder)
        if entry.created.astimezone(zone).date() in allowed
    ]


def undownloaded(entries: list[Entry]) -> list[Entry]:
    history = last_downloads([entry.rel for entry in entries])
    pending = [entry for entry in entries if entry.rel not in history]
    pending.sort(key=lambda entry: (entry.created, entry.rel.casefold()), reverse=True)
    return pending


def parse_days(values: list[str] | None, allowed: set[date]) -> set[date]:
    chosen: set[date] = set()
    for value in values or []:
        try:
            day = date.fromisoformat(value)
        except ValueError:
            continue
        if day in allowed:
            chosen.add(day)
    return chosen


def actor(sess) -> tuple[str, str]:
    user = sess.get("user") or {}
    name = user.get("name") or user.get("email") or "Unknown"
    return name, user.get("oid") or ""


def mark_downloaded(sess, rels: list[str]) -> None:
    name, oid = actor(sess)
    for rel in rels:
        record_download(rel, name, oid)
    sess["grab"] = list(rels)


def grabbed(sess) -> list[str]:
    value = sess.get("grab") or []
    if isinstance(value, str):
        value = [value]
    return [item for item in value if isinstance(item, str) and item]


def refresh_files():
    return Div(
        hx_get="/",
        hx_trigger="load",
        hx_target="#file-list",
        hx_swap="outerHTML",
        hx_include="#path-field,#sort-field,#order-field,#q-field",
    )


def grab_frame():
    token = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return Iframe(
        src=f"/download/grab?t={token}",
        id="grab-download",
        tabindex="-1",
        aria_hidden="true",
        title="File download",
    )


def open_modal(*parts, header, footer=None, body_cls="space-y-6"):
    modal = Modal(
        *parts,
        header=header,
        footer=footer,
        id="download-modal",
        body_cls=body_cls,
        cls="uk-open",
    )
    modal.attrs["class"] = modal.attrs.get("class", "").replace("uk-modal-container", "").strip()
    modal.attrs["hx-on:hidden"] = "this.remove()"
    return modal


def result_modal(rels: list[str]):
    note = (
        f"Downloading {len(rels)} files in one zip."
        if len(rels) != 1
        else "The file is downloading."
    )
    return Div(
        refresh_files(),
        grab_frame(),
        open_modal(
            P(note, cls="text-sm text-muted-foreground"),
            Ul(*[Li(rel) for rel in rels], cls="max-h-64 space-y-1 overflow-auto text-sm"),
            header=ModalTitle("Downloaded"),
            footer=Div(
                A(
                    UkIcon("printer", height=15, width=15),
                    "Print",
                    href="/download/print",
                    target="_blank",
                    cls="uk-btn uk-btn-primary uk-btn-sm inline-flex items-center gap-2",
                ),
                Button("Close", cls=(ButtonT.default, ButtonT.sm, "uk-modal-close"), submit=False),
                cls="flex items-center justify-end gap-2",
            ),
        ),
    )


def day_folder_lines(files: list[Entry]):
    if not files:
        return (Span("No files", cls="block text-xs text-muted-foreground"),)
    counts: dict[str, int] = {}
    for entry in files:
        folder = entry.rel.split("/", 1)[0] if "/" in entry.rel else "Files"
        counts[folder] = counts.get(folder, 0) + 1
    lines = []
    for folder, count in sorted(counts.items(), key=lambda item: item[0].casefold()):
        noun = "file" if count == 1 else "files"
        lines.append(Span(f"{folder}: {count} {noun}", cls="block text-xs text-muted-foreground"))
    return tuple(lines)


def day_picker(entries: list[Entry], now: datetime, zone: timezone, message: str | None = None):
    by_day: dict[date, list[Entry]] = {}
    for entry in entries:
        by_day.setdefault(entry.created.astimezone(zone).date(), []).append(entry)
    rows = []
    any_files = False
    for day in recent_dates(now, zone):
        files = sorted(by_day.get(day, []), key=lambda entry: entry.rel.casefold())
        any_files = any_files or bool(files)
        attrs = {"name": "day", "value": day.isoformat()}
        if not files:
            attrs["disabled"] = "disabled"
        rows.append(fh.Label(
            CheckboxX(**attrs),
            Span(
                Span(day.strftime("%a, %b ") + str(day.day), cls="font-medium"),
                *day_folder_lines(files),
            ),
        ))
    intro = (
        "No new files to download. Would you like to download files from the last two weeks? Select each day to include."
        if any_files
        else "No files from the last 14 days."
    )
    button_attrs = {} if any_files else {"disabled": "disabled"}
    return open_modal(
        P(intro, cls="text-sm"),
        P(message, cls="text-sm text-amber-700 dark:text-amber-300") if message else None,
        Form(
            Div(*rows, cls="day-picker"),
            Div(
                Button("Download selected", cls=(ButtonT.primary, ButtonT.sm), **button_attrs),
                Button("Cancel", cls=(ButtonT.default, ButtonT.sm, "uk-modal-close"), submit=False),
                cls="flex items-center gap-2",
            ),
            id="day-form",
            cls="space-y-4",
            hx_post="/download/days",
            hx_target="#grab-result",
            hx_swap="innerHTML",
            hx_include="#path-field",
            hx_vals=_GRAB_TZ,
        ),
        header=ModalTitle("Download files"),
        body_cls="space-y-4",
    )


def download_button():
    return Button(
        UkIcon("download", height=15, width=15),
        "Download",
        submit=False,
        id="download-newest",
        cls=(ButtonT.primary, ButtonT.sm),
        hx_post="/download/next",
        hx_target="#grab-result",
        hx_swap="innerHTML",
        hx_include="#path-field",
        hx_vals=_GRAB_TZ,
        hx_disabled_elt="this",
        title="Download every file from the last 14 days that has not been downloaded",
    )


@app.get("/login")
def login(sess):
    if sess.get("user"):
        return RedirectResponse("/", status_code=303)
    actions = [A("Sign in with Microsoft", href="/auth/login", cls="uk-btn uk-btn-primary")]
    if DEV_USER:
        actions = [
            A("Continue as test user", href="/auth/dev", cls="uk-btn uk-btn-primary"),
            P("Local test sign-in. Entra is not used.", cls="text-sm"),
        ]
    return (
        Title("Sign in · C1 Imports"),
        Div(theme_toggle(), cls="fixed right-6 top-4 z-50"),
        Main(
            Div(
                Card(
                    logo(),
                    H1("Sign in", cls="text-2xl font-semibold tracking-tight"),
                    P("Use your work account to browse and download mirrored files."),
                    *actions,
                ),
                cls="w-full max-w-md",
            ),
            cls="grid min-h-screen place-items-center px-4",
        ),
    )


@app.get("/auth/dev")
def auth_dev(sess):
    if not DEV_USER:
        raise HTTPException(404)
    sess["user"] = {"oid": "dev", "name": DEV_USER, "email": "dev@localhost"}
    return RedirectResponse("/", status_code=303)


@app.get("/auth/login")
async def auth_login(req):
    try:
        return await oauth.entra.authorize_redirect(req, REDIRECT_URI)
    except Exception:
        log.exception("Could not start Entra sign-in")
        return notice(
            "Sign-in unavailable",
            "Could not reach Entra ID. Check the tenant ID and client settings, then try again.",
        )


@app.get("/auth/callback")
async def auth_callback(req, sess):
    try:
        token = await oauth.entra.authorize_access_token(req)
    except Exception:
        log.exception("Entra sign-in failed")
        return unauthorized("Sign-in failed. Try again, or contact an administrator if it keeps happening.")
    claims = dict(token.get("userinfo") or {})
    if groups_omitted(claims):
        log.info("Sign-in rejected: groups claim omitted for %s", claims.get("preferred_username"))
        return unauthorized(
            "Your account is signed in, but Entra left the groups claim out of the token. "
            "Set the app's groups claim to the security group assigned to this application."
        )
    if not in_group(claims.get("groups")):
        log.info("Sign-in rejected: %s is not in the access group", claims.get("preferred_username"))
        return unauthorized("Your account is not in the group that can access these files.")
    sess["user"] = user_from_claims(claims)
    return RedirectResponse("/", status_code=303)


def notice(title: str, message: str, *actions):
    return (
        Title(f"{title} · C1 Imports"),
        Div(theme_toggle(), cls="fixed right-6 top-4 z-50"),
        Main(
            Div(
                Card(
                    logo(),
                    H1(title, cls="text-2xl font-semibold tracking-tight"),
                    P(message),
                    *actions,
                ),
                cls="w-full max-w-lg",
            ),
            cls="grid min-h-screen place-items-center px-4",
        ),
    )


def unauthorized(message: str):
    return notice(
        "Not authorized",
        message,
        A("Sign out of Microsoft", href=LOGOUT_URL, cls="uk-btn uk-btn-default"),
        A("Try again", href="/auth/login", cls="uk-btn uk-btn-primary"),
    )


@app.get("/unauthorized")
def unauthorized_page():
    return unauthorized("Your account is not in the group that can access these files.")


@app.get("/logout")
def logout(sess):
    sess.clear()
    return RedirectResponse("/login", status_code=303)


def normalize_dir(rel: str) -> str:
    if not rel:
        root()
        return ""
    current = resolve_inside(rel)
    if not current.is_dir():
        raise HTTPException(404)
    relative = current.relative_to(root()).as_posix()
    return "" if relative == "." else relative


@app.get("/")
def index(req, sess, path: str = "", q: str = "", sort: str = "date", order: str = "desc"):
    folder = normalize_dir(path)
    query = q.strip()
    sort, order = normalize_sort(sort, order)
    caption = ""
    if query:
        entries, truncated = search_files(query)
        caption = f'{len(entries)} file{"s" if len(entries) != 1 else ""} matching “{query}”'
        if truncated:
            caption += " (showing the first 500)"
    else:
        _current, entries = list_dir(folder)
    partial = req.headers.get("hx-target") == "file-list"
    table = file_table(
        entries,
        caption,
        show_path=bool(query),
        empty="No matching files" if query else "No files",
        sort=sort,
        order=order,
        path=folder,
        query=query,
        sync_sort_fields=partial,
    )
    if partial:
        return table
    location = P("Search results across all folders.", cls="text-sm text-muted-foreground") if query else breadcrumbs(folder)
    return shell(
        sess,
        "files",
        Div(
            page_heading("Files", "Browse mirrored files, or search every folder by name."),
            sync_panel(),
            cls="mb-6 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between",
        ),
        Div(
            location,
            Div(
                search_box(folder, query, sort, order),
                download_button(),
                cls="flex w-full flex-col gap-3 sm:w-auto sm:flex-row sm:items-center",
            ),
            cls="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between",
        ),
        table,
        Div(id="grab-result"),
    )


@app.get("/download")
def download(sess, path: str = ""):
    if not path:
        raise HTTPException(404)
    target = resolve_inside(path)
    if not target.is_file():
        raise HTTPException(404)
    rel = target.relative_to(root()).as_posix()
    user = sess["user"]
    record_download(rel, user.get("name") or user.get("email") or "Unknown", user.get("oid") or "")
    return FileResponse(target, filename=target.name, content_disposition_type="attachment")


def grab_targets(sess) -> list[tuple[str, object]]:
    found = []
    for rel in grabbed(sess):
        target = resolve_inside(rel)
        if not target.is_file():
            raise HTTPException(404)
        found.append((rel, target))
    if not found:
        raise HTTPException(404)
    return found


@app.post("/download/next")
def download_next(sess, path: str = "", tz: str = "0"):
    folder = normalize_dir(path)
    now = datetime.now(timezone.utc)
    zone = client_zone(tz)
    entries = files_in_window(folder, now, zone)
    pending = undownloaded(entries)
    if not pending:
        return day_picker(entries, now, zone)
    rels = [entry.rel for entry in pending]
    mark_downloaded(sess, rels)
    return result_modal(rels)


@app.post("/download/days")
def download_days(sess, path: str = "", tz: str = "0", day: list[str] = None):
    folder = normalize_dir(path)
    now = datetime.now(timezone.utc)
    zone = client_zone(tz)
    allowed = set(recent_dates(now, zone))
    entries = files_in_window(folder, now, zone)
    chosen_days = parse_days(day, allowed)
    if not chosen_days:
        return day_picker(entries, now, zone, "Select at least one day.")
    picked = [
        entry
        for entry in entries
        if entry.created.astimezone(zone).date() in chosen_days
    ]
    picked.sort(key=lambda entry: (entry.created, entry.rel.casefold()), reverse=True)
    if not picked:
        return day_picker(entries, now, zone, "No files on the selected days.")
    rels = [entry.rel for entry in picked]
    mark_downloaded(sess, rels)
    return result_modal(rels)


def zip_name(file_days: list[date]) -> str:
    unique = sorted(set(file_days), reverse=True)
    if not unique:
        return "c1-import.zip"
    parts = [unique[0].strftime("%m-%d")]
    previous = unique[0]
    for day in unique[1:]:
        if day.month == previous.month and day.year == previous.year:
            parts.append(f".{day.day:02d}")
        else:
            parts.append("." + day.strftime("%m-%d"))
        previous = day
    return "c1-import-" + "".join(parts) + ".zip"


@app.get("/download/grab")
def download_grab(sess):
    paths = grab_targets(sess)
    headers = {"Cache-Control": "no-store"}
    if len(paths) == 1:
        rel, target = paths[0]
        return FileResponse(
            target,
            filename=target.name,
            content_disposition_type="attachment",
            headers=headers,
        )
    buf = BytesIO()
    file_days = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel, target in paths:
            archive.write(target, arcname=rel)
            file_days.append(datetime.fromtimestamp(target.stat().st_mtime).date())
    headers["Content-Disposition"] = f'attachment; filename="{zip_name(file_days)}"'
    return Response(buf.getvalue(), media_type="application/zip", headers=headers)


def print_document(parts: list[tuple[str, str]]) -> str:
    articles = []
    for name, text in parts:
        articles.append(
            f"<article><h1>{html.escape(name)}</h1><pre>{html.escape(text)}</pre></article>"
        )
    body = "\n".join(articles)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Print · C1 Imports</title>
<style>
  html, body {{ background: #fff; color: #111; }}
  body {{ margin: 24px auto; max-width: 800px; font-family: ui-sans-serif, system-ui, sans-serif; }}
  h1 {{ margin: 0 0 12px; font-size: 16px; }}
  pre {{ margin: 0; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, monospace; font-size: 12px; }}
  article {{ break-after: page; page-break-after: always; }}
  article:last-child {{ break-after: auto; page-break-after: auto; }}
  @media screen {{
    article + article {{ margin-top: 24px; padding-top: 24px; border-top: 1px solid #ddd; }}
  }}
</style>
</head>
<body>
{body}
<script>window.addEventListener("load", () => window.print());</script>
</body>
</html>"""


@app.get("/download/print")
def download_print(sess):
    parts = []
    for rel, target in grab_targets(sess):
        parts.append((rel, target.read_text(encoding="utf-8", errors="replace")))
    return HTMLResponse(print_document(parts))


@app.get("/logs")
def logs(req, sess, name: str = ""):
    files = list_logs()
    selected = name or (files[0].name if files else "")
    if selected and not any(path.name == selected for path in files):
        raise HTTPException(404)
    body = log_body(selected)
    if req.headers.get("hx-target") == "log-body":
        return body
    options = [log_option(path.name, path.name == selected) for path in files]
    picker = (
        fh.Select(
            *options,
            name="name",
            cls="uk-select w-full sm:w-72",
            hx_get="/logs",
            hx_target="#log-body",
            hx_swap="outerHTML",
        )
        if options
        else P("No sync logs yet.", cls="text-sm text-muted-foreground")
    )
    return shell(
        sess,
        "logs",
        Div(
            page_heading("Logs", "Each sync writes its own log. The newest one is shown first."),
            sync_panel(),
            cls="mb-6 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between",
        ),
        Div(
            Div(
                P("Sync log", cls="text-sm font-medium"),
                picker,
                cls="flex flex-col gap-2 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between",
            ),
            body,
            cls="overflow-hidden rounded-xl border bg-card",
        ),
    )


def log_stamp(name: str) -> datetime | None:
    match = _LOG_STAMP.match(name)
    if not match:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def log_option(name: str, selected: bool):
    stamp = log_stamp(name)
    if not stamp:
        return Option(name, value=name, selected=selected)
    fallback = stamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    iso = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    return Option(fallback, value=name, selected=selected, data_utc=iso)


def log_line(line: str):
    match = _LOG_LINE.match(line)
    stamp = parse_utc(match["when"]) if match else None
    if not match or stamp is None:
        return Div(line, cls="log-line")
    return Div(
        "[",
        utc_time(stamp),
        f"] [{match['level']}] {match['msg']}",
        cls="log-line",
    )


def log_body(name: str):
    if not name:
        return Div(P("No sync logs yet.", cls="px-4 py-10 text-center text-sm text-muted-foreground"), id="log-body")
    text = read_log(name)
    lines = [log_line(line) for line in text.splitlines()] or [Div("")]
    return Div(
        Div(
            *lines,
            cls="log-panel m-0 max-h-[70vh] overflow-auto p-4 font-mono text-[13px] leading-6",
        ),
        id="log-body",
    )


@app.get("/sync/status")
def sync_status(sess):
    return sync_panel()


@app.post("/sync/run")
def sync_run(sess):
    start_sync()
    return sync_panel()
