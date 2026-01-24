"""YouTube OAuth service for handling Google OAuth 2.0 authorization."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

# Allow scope changes (Google may return additional scopes like openid, profile, email)
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode

logger = logging.getLogger("app.youtube.oauth")

# YouTube readonly scope
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


class YouTubeOAuthService:
    """Handles Google OAuth 2.0 for YouTube API access."""

    def __init__(self) -> None:
        self._client_id = settings.GOOGLE_CLIENT_ID
        self._client_secret = settings.GOOGLE_CLIENT_SECRET
        self._redirect_uri = settings.YOUTUBE_OAUTH_REDIRECT_URI

    def is_configured(self) -> bool:
        """Check if YouTube OAuth is properly configured."""
        return bool(self._client_id and self._client_secret and self._redirect_uri)

    def generate_auth_url(self, state: str) -> str:
        """Generate Google OAuth authorization URL with YouTube scopes.

        Args:
            state: State parameter for CSRF protection (e.g., user_id)

        Returns:
            Authorization URL to redirect the user to
        """
        if not self.is_configured():
            raise BusinessError(
                ErrorCode.YOUTUBE_OAUTH_FAILED,
                reason="YouTube OAuth not configured",
            )

        flow = self._create_flow()
        authorization_url, _ = flow.authorization_url(
            access_type="offline",  # Get refresh_token
            include_granted_scopes="true",
            prompt="consent",  # Force consent to ensure refresh_token
            state=state,
        )

        logger.info(f"Generated YouTube OAuth URL for state={state[:8]}...")
        return authorization_url

    def exchange_code(self, code: str) -> Tuple[str, str, datetime]:
        """Exchange authorization code for tokens.

        Args:
            code: Authorization code from Google callback

        Returns:
            Tuple of (access_token, refresh_token, expires_at)
        """
        if not self.is_configured():
            raise BusinessError(
                ErrorCode.YOUTUBE_OAUTH_FAILED,
                reason="YouTube OAuth not configured",
            )

        try:
            flow = self._create_flow()
            flow.fetch_token(code=code)

            credentials = flow.credentials

            access_token = credentials.token
            refresh_token = credentials.refresh_token
            expires_at = credentials.expiry

            if not refresh_token:
                logger.warning("No refresh_token received from Google")

            # Ensure expires_at has timezone info
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            logger.info("Successfully exchanged code for tokens")
            return access_token, refresh_token or "", expires_at or datetime.now(timezone.utc)

        except Exception as e:
            logger.exception(f"Failed to exchange code: {e}")
            raise BusinessError(
                ErrorCode.YOUTUBE_OAUTH_FAILED,
                reason=str(e),
            )

    def refresh_access_token(self, refresh_token: str) -> Tuple[str, datetime]:
        """Refresh an expired access token.

        Args:
            refresh_token: The refresh token

        Returns:
            Tuple of (new_access_token, new_expires_at)
        """
        if not self.is_configured():
            raise BusinessError(
                ErrorCode.YOUTUBE_OAUTH_FAILED,
                reason="YouTube OAuth not configured",
            )

        try:
            credentials = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._client_id,
                client_secret=self._client_secret,
            )

            credentials.refresh(Request())

            expires_at = credentials.expiry
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            logger.info("Successfully refreshed access token")
            return credentials.token, expires_at or datetime.now(timezone.utc)

        except Exception as e:
            logger.exception(f"Failed to refresh token: {e}")
            raise BusinessError(
                ErrorCode.YOUTUBE_TOKEN_EXPIRED,
                reason=str(e),
            )

    def build_credentials(
        self,
        access_token: str,
        refresh_token: Optional[str],
        expires_at: Optional[datetime] = None,
    ) -> Credentials:
        """Build a Google Credentials object.

        Args:
            access_token: The access token
            refresh_token: The refresh token
            expires_at: Token expiration time

        Returns:
            Google Credentials object
        """
        # Google auth library expects naive datetime (no timezone)
        expiry = None
        if expires_at:
            if expires_at.tzinfo is not None:
                # Convert to UTC and remove timezone info
                expiry = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
            else:
                expiry = expires_at

        return Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self._client_id,
            client_secret=self._client_secret,
            expiry=expiry,
        )

    def is_token_expired(
        self,
        expires_at: Optional[datetime],
        buffer_minutes: int = 5,
    ) -> bool:
        """Check if token is expired or will expire soon.

        Args:
            expires_at: Token expiration time
            buffer_minutes: Consider expired if within this many minutes

        Returns:
            True if token is expired or expiring soon
        """
        if not expires_at:
            return True

        # Ensure timezone aware
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        buffer = timedelta(minutes=buffer_minutes)
        return datetime.now(timezone.utc) >= (expires_at - buffer)

    def _create_flow(self) -> Flow:
        """Create a Google OAuth flow.

        Returns:
            Configured Flow object
        """
        client_config = {
            "web": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [self._redirect_uri],
            }
        }

        flow = Flow.from_client_config(
            client_config,
            scopes=YOUTUBE_SCOPES,
            redirect_uri=self._redirect_uri,
        )

        return flow
