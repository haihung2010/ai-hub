"""Facebook Graph API service for page messaging and content management."""

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

FB_GRAPH_URL = "https://graph.facebook.com/v25.0"


@dataclass
class FacebookPageInfo:
    page_id: str
    page_name: str
    access_token: str


class FacebookService:
    def __init__(self, page_access_token: str):
        self._token = page_access_token
        self._client = httpx.AsyncClient(timeout=30.0)

    async def get_page_info(self) -> FacebookPageInfo:
        """Fetch current page info using the page access token."""
        resp = await self._get("/me")
        return FacebookPageInfo(
            page_id=resp["id"],
            page_name=resp["name"],
            access_token=self._token,
        )

    async def send_text_message(self, recipient_id: str, text: str) -> str:
        """Send a text message to a user via Messenger."""
        payload = {
            "messaging_type": "RESPONSE",
            "recipient": {"id": recipient_id},
            "message": {"text": text},
        }
        resp = await self._post("/me/messages", payload)
        return resp["message_id"]

    async def send_typing_on(self, recipient_id: str) -> bool:
        """Send typing indicator (typing on)."""
        payload = {
            "recipient": {"id": recipient_id},
            "sender_action": "typing_on",
        }
        await self._post("/me/messages", payload)
        return True

    async def send_typing_off(self, recipient_id: str) -> bool:
        """Send typing indicator (typing off)."""
        payload = {
            "recipient": {"id": recipient_id},
            "sender_action": "typing_off",
        }
        await self._post("/me/messages", payload)
        return True

    async def get_user_info(self, user_id: str) -> dict:
        """Fetch Messenger user profile (name, profile pic)."""
        resp = await self._get(f"/{user_id}", fields="name,profile_picture,first_name,last_name")
        return resp

    async def get_page_conversations(self, limit: int = 25) -> list[dict]:
        """List recent conversations (for inbox management)."""
        resp = await self._get("/me/conversations", fields="updated_time,participants,snippet", limit=limit)
        return resp.get("data", [])

    async def mark_seen(self, recipient_id: str) -> bool:
        """Mark message as seen."""
        payload = {
            "recipient": {"id": recipient_id},
            "sender_action": "mark_seen",
        }
        await self._post("/me/messages", payload)
        return True

    async def reply_to_comment(self, comment_id: str, message: str) -> dict:
        """Reply to a comment on a page post."""
        payload = {
            "message": message,
        }
        resp = await self._post(f"/{comment_id}/comments", payload)
        return resp

    async def get_post_comments(self, post_id: str, limit: int = 20) -> list[dict]:
        """Get comments on a page post."""
        resp = await self._get(f"/{post_id}/comments", fields="id,message,from,created_time,parent", limit=limit)
        return resp.get("data", [])

    async def _get(self, path: str, **kwargs) -> dict:
        url = f"{FB_GRAPH_URL}{path}"
        params = {"access_token": self._token}
        params.update(kwargs)
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, data: dict) -> dict:
        url = f"{FB_GRAPH_URL}{path}"
        data["access_token"] = self._token
        resp = await self._client.post(url, json=data)
        resp.raise_for_status()
        return resp.json()

    async def verify_webhook(self, mode: str, verify_token: str, challenge: str, expected_verify_token: str) -> bool:
        """Verify webhook callback URL (Meta webhook verification).
        
        expected_verify_token: the app's known verify token (from env)
        verify_token: the token Facebook sent back for comparison
        """
        if mode == "subscribe" and verify_token == expected_verify_token:
            logger.info("Facebook webhook verified successfully")
            return True
        logger.warning("Facebook webhook verify failed: mode=%s, token_match=%s", mode, verify_token == expected_verify_token)
        return False

    async def close(self):
        await self._client.aclose()