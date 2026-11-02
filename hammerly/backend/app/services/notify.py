from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Notification, User
from ..realtime import hub


async def notify(session: AsyncSession, user_id: int, kind: str, title: str, body: str = "", link: str = ""):
    """Create an in-app notification and push it to the user's socket after commit-safe publish."""
    user = await session.get(User, user_id)
    # Calm Mode removes urgency nudges but keeps transactional messages.
    if user and user.calm_mode and kind in {"ending_soon", "outbid_nudge"}:
        return None
    n = Notification(user_id=user_id, kind=kind, title=title, body=body, link=link)
    session.add(n)
    await session.flush()
    await hub.publish(f"user:{user_id}", {"type": "notification", "id": n.id, "kind": kind,
                                          "title": title, "body": body, "link": link})
    return n
