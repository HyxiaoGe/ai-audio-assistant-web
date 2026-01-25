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

    def get_channel_uploads_playlist_id(self, channel_id: str) -> Optional[str]:
        """Get the uploads playlist ID for a channel.

        The uploads playlist contains all videos uploaded by the channel.
        This is more efficient than using search API (1 unit vs 100 units).

        Args:
            channel_id: YouTube channel ID

        Returns:
            Uploads playlist ID (usually starts with "UU")
        """
        try:
            request = self._youtube.channels().list(
                part="contentDetails",
                id=channel_id,
            )
            response = request.execute()

            items = response.get("items", [])
            if not items:
                logger.warning(f"Channel not found: {channel_id}")
                return None

            content_details = items[0].get("contentDetails", {})
            related_playlists = content_details.get("relatedPlaylists", {})
            uploads_playlist_id = related_playlists.get("uploads")

            logger.debug(f"Channel {channel_id} uploads playlist: {uploads_playlist_id}")
            return uploads_playlist_id

        except HttpError as e:
            logger.exception(f"YouTube API error getting uploads playlist: {e}")
            self._handle_http_error(e)
        except Exception as e:
            logger.exception(f"Unexpected error getting uploads playlist: {e}")
            raise BusinessError(
                ErrorCode.YOUTUBE_API_ERROR,
                reason=str(e),
            )

    def get_playlist_videos(
        self,
        playlist_id: str,
        page_token: Optional[str] = None,
        max_results: int = 50,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Get videos from a playlist (paginated).

        Args:
            playlist_id: YouTube playlist ID (e.g., uploads playlist)
            page_token: Token for pagination
            max_results: Maximum results per page (max 50)

        Returns:
            Tuple of (video list with basic info, next_page_token)
        """
        try:
            request = self._youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=min(max_results, 50),
                pageToken=page_token,
            )
            response = request.execute()

            videos = []
            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                content_details = item.get("contentDetails", {})

                # Skip deleted/private videos
                if snippet.get("title") == "Deleted video":
                    continue
                if snippet.get("title") == "Private video":
                    continue

                videos.append(
                    {
                        "video_id": content_details.get("videoId"),
                        "channel_id": snippet.get("channelId"),
                        "title": snippet.get("title"),
                        "description": snippet.get("description"),
                        "thumbnail_url": self._get_best_thumbnail(snippet.get("thumbnails", {})),
                        "published_at": self._parse_datetime(
                            content_details.get("videoPublishedAt") or snippet.get("publishedAt")
                        ),
                    }
                )

            next_page_token = response.get("nextPageToken")

            logger.debug(
                f"Fetched {len(videos)} videos from playlist {playlist_id}, "
                f"has_more={next_page_token is not None}"
            )

            return videos, next_page_token

        except HttpError as e:
            logger.exception(f"YouTube API error getting playlist videos: {e}")
            self._handle_http_error(e)
        except Exception as e:
            logger.exception(f"Unexpected error getting playlist videos: {e}")
            raise BusinessError(
                ErrorCode.YOUTUBE_API_ERROR,
                reason=str(e),
            )

    def get_videos_details(
        self,
        video_ids: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Batch fetch video details (duration, statistics).

        Args:
            video_ids: List of video IDs (max 50 per call)

        Returns:
            Dict mapping video_id to details (duration_seconds, view_count, etc.)
        """
        if not video_ids:
            return {}

        # API allows max 50 IDs per request
        video_ids = video_ids[:50]

        try:
            request = self._youtube.videos().list(
                part="contentDetails,statistics",
                id=",".join(video_ids),
            )
            response = request.execute()

            result = {}
            for item in response.get("items", []):
                video_id = item.get("id")
                content_details = item.get("contentDetails", {})
                statistics = item.get("statistics", {})

                result[video_id] = {
                    "duration_seconds": self._parse_duration(content_details.get("duration")),
                    "view_count": self._parse_int(statistics.get("viewCount")),
                    "like_count": self._parse_int(statistics.get("likeCount")),
                    "comment_count": self._parse_int(statistics.get("commentCount")),
                }

            logger.debug(f"Fetched details for {len(result)} videos")
            return result

        except HttpError as e:
            logger.exception(f"YouTube API error getting video details: {e}")
            self._handle_http_error(e)
        except Exception as e:
            logger.exception(f"Unexpected error getting video details: {e}")
            raise BusinessError(
                ErrorCode.YOUTUBE_API_ERROR,
                reason=str(e),
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

    def _parse_duration(self, duration_str: Optional[str]) -> Optional[int]:
        """Parse ISO 8601 duration string to seconds.

        Args:
            duration_str: ISO 8601 duration (e.g., "PT1H2M3S")

        Returns:
            Duration in seconds or None
        """
        if not duration_str:
            return None

        try:
            import re

            # Parse ISO 8601 duration format: PT#H#M#S
            match = re.match(
                r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
                duration_str,
            )
            if not match:
                return None

            hours = int(match.group(1) or 0)
            minutes = int(match.group(2) or 0)
            seconds = int(match.group(3) or 0)

            return hours * 3600 + minutes * 60 + seconds
        except (ValueError, AttributeError):
            return None

    def _parse_int(self, value: Optional[str]) -> Optional[int]:
        """Parse string to int, returning None on failure.

        Args:
            value: String value to parse

        Returns:
            Parsed int or None
        """
        if value is None:
            return None

        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    def _get_best_thumbnail(self, thumbnails: Dict[str, Any]) -> Optional[str]:
        """Get the best available thumbnail URL.

        Args:
            thumbnails: Thumbnails dict from YouTube API

        Returns:
            Best thumbnail URL or None
        """
        # Prefer medium > high > default > maxres > standard
        for quality in ["medium", "high", "default", "maxres", "standard"]:
            if quality in thumbnails:
                return thumbnails[quality].get("url")
        return None
