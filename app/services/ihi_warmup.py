# app/services/ihi_warmup.py
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class IHIWarmup:
    """
    Warmup mechanism to prevent cold-start empty responses.
    Pings IHI model with a simple request before first real use.
    """

    def __init__(self, base_url: str = "http://localhost:8083"):
        self.base_url = base_url
        self._warmed = False
        self._lock = asyncio.Lock()

    async def warmup(self, timeout: float = 30.0) -> bool:
        """Warmup IHI model with a simple test request."""
        if self._warmed:
            return True

        async with self._lock:
            if self._warmed:
                return True

            try:
                import httpx
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        json={
                            "model": "local-gemma4-e4b-q4-ihi",
                            "messages": [{"role": "user", "content": "Respond with only: OK"}],
                            "max_tokens": 10,
                            "temperature": 0.1
                        }
                    )
                    if resp.status_code == 200:
                        self._warmed = True
                        logger.info("IHI warmup completed successfully")
                        return True
            except Exception as e:
                logger.warning(f"IHI warmup failed: {e}")
                return False

    def is_warmed(self) -> bool:
        return self._warmed

    def reset(self):
        """Reset warmup state (for testing)."""
        self._warmed = False


# Global warmup instance
_ihi_warmup: Optional[IHIWarmup] = None

def get_ihi_warmup() -> IHIWarmup:
    global _ihi_warmup
    if _ihi_warmup is None:
        _ihi_warmup = IHIWarmup()
    return _ihi_warmup