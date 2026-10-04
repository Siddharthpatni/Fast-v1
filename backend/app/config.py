"""FastMile backend configuration.

Loads settings from environment variables with sensible defaults for local development.
"""

from pathlib import Path

from pydantic_settings import BaseSettings

# Repo root locally (…/Fastmile); "/" in the container, where ./data is mounted at /data.
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── App ──
    app_name: str = "FastMile"
    debug: bool = True
    log_level: str = "info"
    timezone: str = "Europe/Berlin"  # Local time for GTFS service days and disruption windows

    # ── Database ──
    database_url: str = "postgresql+asyncpg://fastmile:fastmile_dev@localhost:5432/fastmile"

    # ── Redis ──
    redis_url: str = "redis://localhost:6379/0"

    # ── Rate limits (requests per minute per client IP) ──
    rate_limit_enabled: bool = True
    rate_limit_reports_per_min: int = 10  # Each report can cost an LLM and a Jev call
    rate_limit_plans_per_min: int = 60
    rate_limit_geocode_per_min: int = 60  # Photon is a shared free service

    # ── Provider data ──
    gtfs_path: str = str(DATA_DIR / "gtfs_dev")  # GTFS directory or .zip
    gtfs_rt_url: str = ""  # GTFS-Realtime feed: delays, cancellations, service alerts (optional)
    gtfs_rt_refresh_seconds: int = 300  # The free nationwide feed is ~50 MB; don't poll it hard
    gbfs_feeds: str = str(DATA_DIR / "gbfs_dev")  # Comma-separated gbfs.json URLs or local dirs
    gbfs_refresh_seconds: int = 60
    on_demand_zones_path: str = str(DATA_DIR / "on_demand_zones.json")
    # Street routing for walk/bike/car legs: "osrm" or "none" (straight-line estimates).
    # Default: FOSSGIS public OSRM servers (free, no key, fair use). {profile} = foot|bike|car
    street_routing: str = "osrm"
    street_routing_url: str = "https://routing.openstreetmap.de/routed-{profile}"

    # ── Germany-wide transit routing for trips outside the loaded feed ──
    # Transitous: free community MOTIS server (https://transitous.org). "" = loaded region only
    transitous_url: str = "https://api.transitous.org"

    # ── Address search (Photon, free OSM geocoder; allows search-as-you-type) ──
    geocoder_url: str = "https://photon.komoot.io/api/"
    geocoder_bias_lat: float = 51.88  # Results near the Harz region rank first...
    geocoder_bias_lon: float = 10.43
    geocoder_bias_scale: float = 0.3  # ...but only mildly: any place in Germany is plannable

    # ── ML models ──
    capacity_model_path: str = str(DATA_DIR / "models" / "capacity.joblib")
    wait_model_path: str = str(DATA_DIR / "models" / "on_demand_wait.joblib")

    # ── LLM (text generation — disruption extraction) ──
    # "ollama", "openai", "openrouter", a fallback chain like "openrouter,ollama",
    # or "none" (rule-based extraction only)
    llm_provider: str = "ollama"
    llm_model: str = ""  # Empty = the provider's default (see llm.client.DEFAULT_MODELS)
    llm_timeout: float = 120.0  # The first Ollama call also loads the model
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_base_url: str = "http://localhost:11434"
    openrouter_api_key: str = ""
    # Paid OpenRouter model tried after the free ones fail (e.g. their 50-a-day limit for
    # accounts with under $10 bought). Capped per UTC day so it can't drain the credit.
    llm_paid_model: str = ""
    llm_paid_daily_limit: int = 200
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ── Site operator, shown in the Impressum (§ 5 DDG) and the privacy notice ──
    # Required before the site is public in Germany. Not secrets; use "\n" for line breaks.
    operator_name: str = ""
    operator_address: str = ""
    operator_email: str = ""
    operator_phone: str = ""

    # ── Jev / TypeSafe AI (fast decisions — no text generation) ──
    jev_api_key: str = ""
    jev_model: str = "jev-latest"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
