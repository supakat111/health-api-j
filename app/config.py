"""Configuration — reads everything from environment variables.

None of these values live in the code. On Render they are set in the
service's Environment tab; locally you can put them in a .env file
(which is gitignored) if you ever want to run it on your own machine.
"""
import os


class Settings:
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    supabase_url: str = os.environ.get("SUPABASE_URL", "")
    supabase_service_key: str = os.environ.get("SUPABASE_SERVICE_KEY", "")
    app_password: str = os.environ.get("APP_PASSWORD", "")

    # Storage bucket where the original PDFs are kept
    bucket: str = "bloodwork"

    # Claude model used for extraction. Haiku is cheap and accurate for this.
    model: str = "claude-haiku-4-5-20251001"


settings = Settings()
