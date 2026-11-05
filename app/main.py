import logging
import re
from urllib.parse import urlencode

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, RedirectResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import fasthtml.common as fh
from fasthtml.common import *
from fasthtml.svg import Path, Rect
from monsterui.all import *

from app.auth import groups_omitted, in_group, oauth, user_from_claims
from app.browser import Entry, list_dir, resolve_inside, root, search_files
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
.file-table td, .file-table th { vertical-align: middle; }
.log-panel { background: hsl(var(--muted) / 0.45); }
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


def download_bits(info):
    if not info:
        return Span("—", cls="text-muted-foreground")
    return Div(
        Span(info["downloaded_by"], cls="block"),
        Span(info["downloaded_at"], cls="block text-xs text-muted-foreground"),
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


def named_link(icon, label, href):
    return A(
        UkIcon(icon, height=16, width=16, cls="shrink-0 text-muted-foreground"),
        Span(label, cls="truncate"),
        href=href,
        cls="inline-flex max-w-full items-center gap-2",
    )


def file_rows(entries: list[Entry], history: dict, show_path: bool = False, empty: str = "No files") -> list:
    if not entries:
        return [Tr(Td(
            P(empty, cls="py-10 text-center text-sm text-muted-foreground"),
            colspan="4",
        ))]
    rows = []
    for entry in entries:
        label = entry.rel if show_path else entry.name
        if entry.is_dir:
            name = named_link("folder", label, "/?" + urlencode({"path": entry.rel}))
            size = Span("—", cls="text-muted-foreground")
            downloaded = Span("—", cls="text-muted-foreground")
        else:
            name = named_link("file", label, "/download?" + urlencode({"path": entry.rel}))
            size = human_size(entry.size or 0)
            downloaded = download_bits(history.get(entry.rel))
        rows.append(Tr(
            Td(name),
            Td(size, cls="whitespace-nowrap text-sm text-muted-foreground"),
            Td(entry.modified, cls="whitespace-nowrap text-sm text-muted-foreground"),
            Td(downloaded),
        ))
    return rows


def file_table(entries: list[Entry], caption: str = "", show_path: bool = False, empty: str = "No files"):
    files = [entry.rel for entry in entries if not entry.is_dir]
    history = last_downloads(files)
    head = P(caption, cls="border-b px-4 py-3 text-sm text-muted-foreground") if caption else None
    return Div(
        head,
        Div(
            Table(
                Thead(Tr(
                    Th("Name"),
                    Th("Size"),
                    Th("Modified"),
                    Th("Last download"),
                )),
                Tbody(*file_rows(entries, history, show_path=show_path, empty=empty)),
            ),
            cls="file-table overflow-x-auto",
        ),
        id="file-list",
        cls="overflow-hidden rounded-xl border bg-card",
    )


def pretty_last_run(summary: str) -> tuple[str, str]:
    match = _LOG_LINE.match(summary.strip())
    if not match:
        return "", summary
    message = match["msg"]
    if "completed in" in message:
        message = message.replace("SFTP sync process completed in", "Completed in")
    elif "already in progress" in message:
        message = "A sync was already running"
    elif message == "SFTP sync completed successfully":
        message = "Sync finished"
    return match["when"], message


def sync_panel():
    running, pid = sync_state()
    when, detail = pretty_last_run(last_sync_summary())
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
            P(when, cls="mt-1 text-xs text-muted-foreground") if when else None,
        ),
        P(detail, cls="min-w-0 flex-1 truncate text-sm text-muted-foreground", title=detail),
        Button(UkIcon("refresh-cw", height=15, width=15), "Run sync", **button),
        id="sync-status",
        hx_get="/sync/status",
        hx_trigger="every 2s",
        hx_swap="outerHTML",
        cls="flex w-full flex-col gap-3 rounded-xl border bg-card px-4 py-3 sm:flex-row sm:items-center lg:max-w-xl",
    )


def search_box(path: str, query: str):
    return Form(
        Input(
            type="search",
            name="q",
            value=query,
            placeholder="Search file names",
            hx_get="/",
            hx_trigger="keyup changed delay:300ms, search",
            hx_target="#file-list",
            hx_swap="outerHTML",
            hx_include="#path-field",
        ),
        Input(type="hidden", name="path", value=path, id="path-field"),
        cls="w-full sm:max-w-sm",
    )


def page_heading(title, subtitle):
    return Div(
        H1(title, cls="text-2xl font-semibold tracking-tight"),
        P(subtitle, cls="text-sm text-muted-foreground"),
        cls="space-y-1",
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
def index(req, sess, path: str = "", q: str = ""):
    folder = normalize_dir(path)
    query = q.strip()
    caption = ""
    if query:
        entries, truncated = search_files(query)
        caption = f'{len(entries)} file{"s" if len(entries) != 1 else ""} matching “{query}”'
        if truncated:
            caption += " (showing the first 500)"
    else:
        _current, entries = list_dir(folder)
    table = file_table(
        entries,
        caption,
        show_path=bool(query),
        empty="No matching files" if query else "No files",
    )
    if req.headers.get("hx-target") == "file-list":
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
            search_box(folder, query),
            cls="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between",
        ),
        table,
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


@app.get("/logs")
def logs(req, sess, name: str = ""):
    files = list_logs()
    selected = name or (files[0].name if files else "")
    if selected and not any(path.name == selected for path in files):
        raise HTTPException(404)
    body = log_body(selected)
    if req.headers.get("hx-target") == "log-body":
        return body
    options = [Option(log_label(path.name), value=path.name, selected=path.name == selected) for path in files]
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


def log_label(name: str) -> str:
    match = _LOG_STAMP.match(name)
    if not match:
        return name
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day} {hour}:{minute}:{second}"


def log_body(name: str):
    if not name:
        return Div(P("No sync logs yet.", cls="px-4 py-10 text-center text-sm text-muted-foreground"), id="log-body")
    return Div(
        Pre(
            Code(read_log(name)),
            cls="log-panel m-0 max-h-[70vh] overflow-auto p-4 font-mono text-[13px] leading-6 whitespace-pre-wrap",
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
