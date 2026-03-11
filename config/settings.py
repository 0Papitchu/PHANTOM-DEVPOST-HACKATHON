# phantom-ui-navigator/config/settings.py
# Configuration centralisée — variables d'env + valeurs par défaut
"""
Phantom UI Navigator — Settings
Toute la config passe par ici. Aucun secret en dur.
Utilise pydantic-settings pour validation + .env auto-load.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    """Configuration principale de Phantom UI Navigator."""

    # ── GCP ──────────────────────────────────────────────
    gcp_project_id: str = Field(
        default="phantom-ui-navigator",
        description="Google Cloud project ID",
    )
    gcp_region: str = Field(
        default="us-central1",
        description="Région GCP par défaut",
    )
    google_application_credentials: Optional[str] = Field(
        default="./phantom-sa-key.json",
        description="Chemin vers la clé du Service Account",
    )

    # ── Gemini AI ────────────────────────────────────────
    gemini_api_key: Optional[str] = Field(
        default=None,
        description="Clé API Gemini (ou via Secret Manager)",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description="Modèle Gemini à utiliser",
    )
    gemini_secret_name: str = Field(
        default="projects/157502772725/secrets/GEMINI_API_KEY/versions/2",
        description="Chemin complet du secret Gemini dans Secret Manager",
    )

    # ── Cloud Storage ────────────────────────────────────
    storage_bucket: str = Field(
        default="phantom-screenshots-157502772725",
        description="Bucket GCS pour les screenshots",
    )

    # ── Pub/Sub Topics ───────────────────────────────────
    pubsub_topic_screenshots: str = Field(
        default="phantom-screenshots",
        description="Topic Pub/Sub pour les events de screenshot",
    )
    pubsub_topic_analysis: str = Field(
        default="phantom-analysis",
        description="Topic Pub/Sub pour les résultats d'analyse",
    )
    pubsub_topic_actions: str = Field(
        default="phantom-actions",
        description="Topic Pub/Sub pour les actions à exécuter",
    )

    # ── Firestore ────────────────────────────────────────
    firestore_database: str = Field(
        default="(default)",
        description="Base Firestore (default = base principale)",
    )

    # ── Screenshot Agent ─────────────────────────────────
    screenshot_interval_seconds: float = Field(
        default=2.0,
        description="Intervalle entre chaque capture d'écran (secondes)",
    )
    screenshot_format: str = Field(
        default="png",
        description="Format des screenshots (png, jpeg)",
    )

    # ── Playwright / Browser ─────────────────────────────
    browser_headless: bool = Field(
        default=True,
        description="Mode headless pour Playwright",
    )
    browser_default_url: str = Field(
        default="https://www.google.com",
        description="URL de démarrage par défaut du navigateur",
    )
    browser_viewport_width: int = Field(
        default=1280,
        description="Largeur du viewport",
    )
    browser_viewport_height: int = Field(
        default=720,
        description="Hauteur du viewport",
    )

    # ── API Server ───────────────────────────────────────
    api_host: str = Field(
        default="0.0.0.0",
        description="Host du serveur FastAPI",
    )
    api_port: int = Field(
        default=8000,
        description="Port du serveur FastAPI",
    )
    api_reload: bool = Field(
        default=True,
        description="Hot reload pour le dev",
    )
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000",
        description="Origines CORS autorisées (séparées par des virgules)",
    )

    # ── Logging ──────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        description="Niveau de log (DEBUG, INFO, WARNING, ERROR)",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }

    @property
    def cors_origins_list(self) -> list[str]:
        """Retourne les origines CORS sous forme de liste."""
        return [origin.strip() for origin in self.cors_origins.split(",")]


# ── Singleton ────────────────────────────────────────────
# Importer `settings` partout pour accéder à la config
settings = Settings()
