"""YouTube Data API service for interacting with YouTube Data API v3."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, NoReturn, Optional, Tuple

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode

logger = logging.getLogger("app.youtube.data")


class YouTubeDataService:
    """Interacts with YouTube Data API v3."""

    def __init__(self, credentials: Credentials) -> None:
        """Initialize the service with Google credentials.

        Args:
            credentials: Google OAuth credentials
        """
        self._youtube = build("youtube", "v3", credentials=credentials)

    def get_my_channel(self) -> Dict[str, Any]:
        """Get the authenticated user's channel information.

        Returns:
            Channel information dict with id, title, thumbnail, etc.
        """
        try:
            request = self._youtube.channels().list(part="snippet", mine=True)
            response = request.execute()

            items = response.get("items", [])
            if not items:
                raise BusinessError(
                    ErrorCode.YOUTUBE_API_ERROR,
                    reason="No channel found for authenticated user",
                )

            channel = items[0]
            snippet = channel.get("snippet", {})

            return {
                "id": channel.get("id"),
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url"),
            }

        except HttpError as e:
            logger.exception(f"YouTube API error: {e}")
            self._handle_http_error(e)
        except BusinessError:
            raise
        except Exception as e:
            logger.exception(f"Unexpected error getting channel: {e}")
            raise BusinessError(
                ErrorCode.YOUTUBE_API_ERROR,
                reason=str(e),
            )

    def get_subscriptions(
        self,
        page_token: Optional[str] = None,
        max_results: int = 50,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get user's subscriptions (paginated).

        Args:
            page_token: Token for pagination
            max_results: Maximum results per page (max 50)

        Returns:
            Tuple of (subscriptions list, next_page_token)
        """
        try:
            request = self._youtube.subscriptions().list(
                part="snippet,contentDetails",
                mine=True,
                maxResults=min(max_results, 50),
                pageToken=page_token,
                order="alphabetical",
            )
            response = request.execute()

            subscriptions = []
            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                resource_id = snippet.get("resourceId", {})

                subscriptions.append(
                    {
                        "channel_id": resource_id.get("channelId"),
                        "channel_title": snippet.get("title"),
                        "channel_description": snippet.get("description"),
                        "channel_thumbnail": snippet.get("thumbnails", {})
                        .get("default", {})
                        .get("url"),
                        "subscribed_at": self._parse_datetime(snippet.get("publishedAt")),
                    }
                )

            next_page_token = response.get("nextPageToken")

            logger.debug(
                f"Fetched {len(subscriptions)} subscriptions, "
                f"has_more={next_page_token is not None}"
            )

            return subscriptions, next_page_token

        except HttpError as e:
            logger.exception(f"YouTube API error: {e}")
            self._handle_http_error(e)
        except BusinessError:
            raise
        except Exception as e:
            logger.exception(f"Unexpected error getting subscriptions: {e}")
            raise BusinessError(
                ErrorCode.YOUTUBE_API_ERROR,
                reason=str(e),
            )

    def get_all_subscriptions(self) -> List[Dict[str, Any]]:
        """Get all user's subscriptions (handles pagination).

        Returns:
            Complete list of all subscriptions
        """
        all_subscriptions = []
        page_token = None

        while True:
            subscriptions, next_page_token = self.get_subscriptions(
                page_token=page_token,
                max_results=50,
            )
            all_subscriptions.extend(subscriptions)

            if not next_page_token:
                break

            page_token = next_page_token

        logger.info(f"Fetched total {len(all_subscriptions)} subscriptions")
        return all_subscriptions

    def _handle_http_error(self, error: HttpError) -> NoReturn:
        """Handle YouTube API HTTP errors.

        Args:
            error: The HttpError from the API
        """
        status = error.resp.status
        content = str(error)

        if status == 401:
            raise BusinessError(
                ErrorCode.YOUTUBE_TOKEN_EXPIRED,
                reason="Access token expired or invalid",
            )
        elif status == 403:
            if "quotaExceeded" in content:
                raise BusinessError(
                    ErrorCode.YOUTUBE_API_ERROR,
                    reason="YouTube API quota exceeded",
                )
            raise BusinessError(
                ErrorCode.YOUTUBE_API_ERROR,
                reason="Access forbidden - check API permissions",
            )
        else:
            raise BusinessError(
                ErrorCode.YOUTUBE_API_ERROR,
                reason=f"YouTube API error (HTTP {status}): {content}",
            )

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime string from YouTube API.

        Args:
            dt_str: ISO format datetime string

        Returns:
            Parsed datetime or None
        """
        if not dt_str:
            return None

        try:
            # YouTube returns ISO format like "2024-01-15T10:30:00.000Z"
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
