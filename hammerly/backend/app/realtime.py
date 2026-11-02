"""In-process pub/sub hub for WebSocket fan-out.

Channels: listing:{id}, room:{id}, user:{id}.
For several API processes, replace publish() with Redis pub/sub; the call sites stay the same.
"""
import asyncio
import json
from collections import defaultdict

from fastapi import WebSocket


class Hub:
    def __init__(self):
        self.channels: dict[str, set[WebSocket]] = defaultdict(set)

    async def join(self, channel: str, ws: WebSocket):
        self.channels[channel].add(ws)

    def leave(self, channel: str, ws: WebSocket):
        self.channels[channel].discard(ws)
        if not self.channels[channel]:
            self.channels.pop(channel, None)

    def count(self, channel: str) -> int:
        return len(self.channels.get(channel, ()))

    async def publish(self, channel: str, event: dict):
        text = json.dumps(event, default=str)
        dead = []
        for ws in list(self.channels.get(channel, ())):
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=2)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(channel, ws)


hub = Hub()
