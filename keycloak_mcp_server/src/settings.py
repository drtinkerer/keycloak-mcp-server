"""Settings for the Keycloak MCP Server."""

from typing import List, Optional

from dotenv import find_dotenv, load_dotenv
from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings

from keycloak_mcp_server.utils.pylogger import get_python_logger

# Initialize logger
logger = get_python_logger()

# Load environment variables with error handling
try:
    # Search from the working directory: without usecwd, python-dotenv searches
    # from this file's directory, which for an installed package (uvx, pip)
    # is site-packages, so a .env next to the user would never be read.
    load_dotenv(find_dotenv(usecwd=True))
except Exception as e:
    # Log error but don't fail - environment variables might be set directly
    logger.warning(f"Could not load .env file: {e}")


class Settings(BaseSettings):
    """Configuration settings for the Keycloak MCP Server.

    Uses Pydantic BaseSettings to load and validate configuration from environment variables.
    Provides default values for optional settings and validation for required ones.
    """

    KEYCLOAK_SERVER_URL: Optional[AnyHttpUrl] = None
    KEYCLOAK_REALM: str = ""
    KEYCLOAK_TIMEOUT: int = Field(default=30, ge=1, le=60)
    KEYCLOAK_CA_BUNDLE: Optional[str] = None
    KEYCLOAK_DEV_ACCESS_TOKEN: Optional[SecretStr] = None
    KEYCLOAK_OAUTH_CLIENT_ID: str = ""
    KEYCLOAK_OAUTH_CLIENT_SECRET: Optional[SecretStr] = None
    KEYCLOAK_OAUTH_SIGNING_KEY: Optional[SecretStr] = None
    KEYCLOAK_OAUTH_AUDIENCE: str = "keycloak-mcp"
    KEYCLOAK_OAUTH_SCOPES: str = "openid profile email"
    KEYCLOAK_OAUTH_STORAGE_DIR: str = ".keycloak-oauth"
    KEYCLOAK_OAUTH_CLIENT_REDIRECT_URIS: List[str] = Field(
        default_factory=lambda: ["http://localhost:*/*", "http://127.0.0.1:*/*"]
    )
    # stdio mode: the server signs the user in itself via this loopback callback.
    KEYCLOAK_LOGIN_PORT: int = Field(default=8250, ge=1024, le=65535)
    KEYCLOAK_TOKEN_CACHE_DIR: str = "~/.cache/keycloak-mcp-server"

    @model_validator(mode="after")
    def validate_keycloak_url(self) -> "Settings":
        """Keep credentials and query strings out of the configured server URL."""
        url = self.KEYCLOAK_SERVER_URL
        if url and (url.username or url.password or url.query or url.fragment):
            raise ValueError(
                "KEYCLOAK_SERVER_URL must not contain credentials, a query, or a fragment"
            )
        return self

    MCP_HOST: str = Field(
        default="localhost",
        json_schema_extra={
            "env": "MCP_HOST",
            "description": "Host address for the MCP server",
            "example": "localhost",
        },
    )
    MCP_PORT: int = Field(
        default=5001,
        ge=1024,
        le=65535,
        json_schema_extra={
            "env": "MCP_PORT",
            "description": "Port number for the MCP server",
            "example": 5001,
        },
    )
    MCP_SSL_KEYFILE: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "env": "MCP_SSL_KEYFILE",
            "description": "Path to SSL private key file for HTTPS",
            "example": "/path/to/key.pem",
        },
    )
    MCP_SSL_CERTFILE: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "env": "MCP_SSL_CERTFILE",
            "description": "Path to SSL certificate file for HTTPS",
            "example": "/path/to/cert.pem",
        },
    )
    MCP_TRANSPORT_PROTOCOL: str = Field(
        default="http",
        json_schema_extra={
            "env": "MCP_TRANSPORT_PROTOCOL",
            "description": "Transport protocol for the MCP server",
            "example": "streamable-http",
            "enum": ["streamable-http", "sse", "http", "stdio"],
        },
    )
    PYTHON_LOG_LEVEL: str = Field(
        default="INFO",
        json_schema_extra={
            "env": "PYTHON_LOG_LEVEL",
            "description": "Logging level for the application",
            "example": "INFO",
            "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        },
    )
    CORS_ENABLED: bool = Field(
        default=False,
        json_schema_extra={
            "env": "CORS_ENABLED",
            "description": "Enable CORS for the MCP server",
            "example": True,
        },
    )
    CORS_ORIGINS: List[str] = Field(
        default=["*"],
        json_schema_extra={
            "env": "CORS_ORIGINS",
            "description": "Origins allowed to access the MCP server",
            "example": ["*"],
        },
    )
    CORS_CREDENTIALS: bool = Field(
        default=True,
        json_schema_extra={
            "env": "CORS_CREDENTIALS",
            "description": "Allow credentials for CORS requests",
            "example": True,
        },
    )
    CORS_METHODS: List[str] = Field(
        default=["*"],
        json_schema_extra={
            "env": "CORS_METHODS",
            "description": "Methods allowed for CORS requests",
            "example": ["*"],
        },
    )
    CORS_HEADERS: List[str] = Field(
        default=["*"],
        json_schema_extra={
            "env": "CORS_HEADERS",
            "description": "Headers allowed for CORS requests",
            "example": ["*"],
        },
    )
    MCP_HOST_ENDPOINT: str = Field(
        default="http://localhost:5001",
        json_schema_extra={
            "env": "MCP_HOST_ENDPOINT",
            "description": "Host endpoint for the MCP server",
            "example": "http://localhost:5001",
        },
    )
    ENVIRONMENT: str = Field(
        default="development",
        json_schema_extra={
            "env": "ENVIRONMENT",
            "description": "Environment for the MCP server",
            "example": "development",
        },
    )
    ENABLE_AUTH: bool = Field(
        default=True,
        json_schema_extra={
            "env": "ENABLE_AUTH",
            "description": "Enable authentication for the MCP server",
            "example": "true",
        },
    )


def validate_config(settings: Settings) -> None:
    """Validate configuration settings.

    Performs validation to ensure required settings are present and values
    are within acceptable ranges.

    Args:
        settings: Settings instance to validate.

    Raises:
        ValueError: If required configuration is missing or invalid.
    """
    # Validate port range
    if not (1024 <= settings.MCP_PORT <= 65535):
        raise ValueError(
            f"MCP_PORT must be between 1024 and 65535, got {settings.MCP_PORT}"
        )

    # Validate log level
    valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if settings.PYTHON_LOG_LEVEL.upper() not in valid_log_levels:
        raise ValueError(
            f"PYTHON_LOG_LEVEL must be one of {valid_log_levels}, got {settings.PYTHON_LOG_LEVEL}"
        )

    # Validate transport protocol
    valid_transport_protocols = ["streamable-http", "sse", "http", "stdio"]
    if settings.MCP_TRANSPORT_PROTOCOL not in valid_transport_protocols:
        raise ValueError(
            f"MCP_TRANSPORT_PROTOCOL must be one of {valid_transport_protocols}, got {settings.MCP_TRANSPORT_PROTOCOL}"
        )


# Create config instance without validation (validation happens in main.py)
settings = Settings()
