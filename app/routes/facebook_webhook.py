"""POST /webhooks/facebook — webhook endpoint for Messenger and page events."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Query, Request, HTTPException
from fastapi.responses import PlainTextResponse

from app.models.chat import ChatRequest
from app.services.facebook_service import FacebookService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _get_fb_service() -> FacebookService:
    token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=500, detail="FACEBOOK_PAGE_ACCESS_TOKEN not configured")
    return FacebookService(token)


async def _build_ai_response(sender_id: str, message_text: str, request: Request) -> str:
    """Call AI Hub chat service with the incoming message and return the bot reply."""
    ai_service = request.app.state.ai_service
    try:
        chat_req = ChatRequest(
            project_id="facebook",
            tenant_id="default",
            user_name=f"fb_{sender_id}",
            user_message=message_text,
            model_mode="lite",
        )
        result = await ai_service.chat(chat_req)
        return result.content
    except Exception as exc:
        logger.error(f"AI Hub chat failed: {exc}")
        return "Xin lỗi, bot đang bận. Vui lòng thử lại sau."


@router.get("/facebook")
async def webhook_verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    """Webhook verification GET from Meta/Facebook.
    
    Facebook sends GET to verify the callback URL.
    Respond with hub.challenge to confirm.
    """
    expected_token = os.environ.get("FACEBOOK_WEBHOOK_VERIFY_TOKEN", "")
    if not expected_token:
        raise HTTPException(status_code=500, detail="FACEBOOK_WEBHOOK_VERIFY_TOKEN not configured")
    fb_service = _get_fb_service()
    if await fb_service.verify_webhook(hub_mode, hub_verify_token, hub_challenge, expected_token):
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/facebook")
async def webhook_event(request: Request, body: dict[str, Any] = {}):
    """Receive webhook events from Facebook (messages, comments, etc.)."""
    if body.get("object") != "page":
        return {"status": "ignored"}

    entries = body.get("entry", [])
    
    for entry in entries:
        for event in entry.get("messaging", []):
            await _handle_messaging_event(event, request)
        for event in entry.get("changes", []):
            await _handle_page_change_event(event, request)

    return {"status": "ok"}


async def _handle_messaging_event(event: dict, request: Request):
    """Process a messaging webhook event (inbox message)."""
    sender_id = event.get("sender", {}).get("id", "")
    recipient_id = event.get("recipient", {}).get("id", "")
    message = event.get("message", {})
    
    if sender_id == recipient_id:
        return  # ignore self-messages

    message_text = message.get("text", "")
    message_id = message.get("mid", "")
    is_echo = message.get("is_echo", False)

    if is_echo:
        logger.debug(f"Echo message ignored: {message_id}")
        return

    if not message_text:
        logger.info(f"[FB] Non-text message from {sender_id}, skipped")
        return

    logger.info(f"[FB Messenger] From {sender_id}: {message_text[:80]}")

    fb_service = _get_fb_service()

    try:
        await fb_service.mark_seen(sender_id)
        await fb_service.send_typing_on(sender_id)

        reply_text = await _build_ai_response(sender_id, message_text, request)
        
        if reply_text:
            await fb_service.send_typing_off(sender_id)
            await fb_service.send_text_message(sender_id, reply_text)
            logger.info(f"[FB Reply] To {sender_id}: {reply_text[:80]}")

    except Exception as exc:
        logger.error(f"[FB Messenger] Error: {exc}")
        try:
            await fb_service.send_text_message(sender_id, "Xin lỗi, bot đang gặp sự cố. Vui lòng thử lại sau.")
        except Exception:
            pass


async def _handle_page_change_event(event: dict, request: Request):
    """Process page change events (comments, mentions)."""
    field = event.get("field", "")
    value = event.get("value", {})
    
    if field != "feed":
        return

    item = value.get("item", "")
    verb = value.get("verb", "")
    
    if item == "comment" and verb == "add":
        comment_id = value.get("comment_id", "")
        message_text = value.get("message", "")
        post_id = value.get("post_id", "")
        
        logger.info(f"[FB Comment] On post {post_id}: {message_text[:80]}")
        
        try:
            ai_service = request.app.state.ai_service
            chat_req = ChatRequest(
                project_id="facebook",
                tenant_id="default",
                user_name=f"fb_comment_{value.get('from', {}).get('id', 'unknown')}",
                user_message=f"[Comment on post {post_id}]: {message_text}",
                model_mode="lite",
            )
            reply_text = await ai_service.chat(chat_req)
            if reply_text.content:
                fb_service = _get_fb_service()
                await fb_service.reply_to_comment(comment_id, reply_text.content)
                logger.info(f"[FB Comment Reply] To {comment_id}: {reply_text.content[:80]}")
        except Exception as exc:
            logger.error(f"[FB Comment] Error: {exc}")