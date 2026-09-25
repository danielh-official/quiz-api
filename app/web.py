"""The home page: how to connect an AI and run your own copy. No sign-in; MCP clients and /docs do their own OAuth."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app import config

TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def home() -> str:
    return TEMPLATES.get_template("home.html").render(app_url=config.APP_URL, plugin_marketplace=config.PLUGIN_MARKETPLACE)


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    """Keep crawlers off: a sign-in page on a shared host domain is what phishing scanners go looking for."""
    return "User-agent: *\nDisallow: /\n"
