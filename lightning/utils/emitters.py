"""
Lightning.py - A Discord bot
Copyright (C) 2019-2021 LightSage

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation at version 3 of the License.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import asyncio
from typing import NamedTuple, Optional

import aiohttp
import discord
from discord.ext.tasks import Loop
from discord.utils import MISSING


class Emitter:
    """Base emitter"""
    def __init__(self, *, loop: asyncio.AbstractEventLoop = None, task_name: Optional[str] = None, **kwargs):
        self._queue = asyncio.Queue(loop=loop)
        kwargs = {'seconds': 0, 'minutes': 0, 'hours': 0, 'time': MISSING, 'count': None}
        self.task = Loop(self.emit_loop, reconnect=True, loop=loop, **kwargs)
        self.task_name = task_name

    def start(self) -> None:
        self.task.start()

    @property
    def closed(self):
        return self._task.cancelled() if self._task else True

    def close(self) -> None:
        self.task.cancel()

    def running(self) -> bool:
        return self.task.is_running

    # def get_task(self):
    #    return self._task

    async def emit_loop(self):
        await self._emit()

    async def _emit(self):
        raise NotImplementedError


class WebhookEmbedEmitter(Emitter):
    """An emitter designed for webhooks sending embeds"""
    def __init__(self, url: str, *, session: aiohttp.ClientSession = None, **kwargs):
        self.session = session or aiohttp.ClientSession()
        self.webhook = discord.Webhook.from_url(url, session=self.session)
        super().__init__(**kwargs)

    async def put(self, embed: discord.Embed) -> None:
        await self._queue.put(embed)

    async def _emit(self):
        while self.task.is_running:
            embed = await self._queue.get()
            embeds = [embed]
            await asyncio.sleep(5)

            size = self._queue.qsize()
            for _ in range(min(9, size)):
                embeds.append(self._queue.get_nowait())

            await self.webhook.send(embeds=embeds)


class Stats(NamedTuple):
    sent: int
    pending: int


class TextChannelEmitter(Emitter):
    """An emitter designed for a text channel"""
    def __init__(self, channel: discord.TextChannel, **kwargs):
        super().__init__(task_name=f"textchannel-emitter-{channel.id}", **kwargs)
        self.channel = channel
        self._stats = (0,)

    def get_stats(self) -> Stats:
        return Stats(self._stats[0], self._queue.qsize())

    async def put(self, content=None, **kwargs):
        x = {'content': content, **kwargs}
        await self._queue.put(x)

    async def send(self, *args, **kwargs):
        """Alias function for TextChannelEmitter.put"""
        await self.put(*args, **kwargs)

    async def _emit(self):
        while self.task.is_running:
            msg = await self._queue.get()

            try:
                await self.channel.send(**msg)
                self._stats[0] += 1
            except discord.NotFound:
                self.close()

            # Rough estimate to wait before sending again without hitting ratelimits.
            # We may need to rethink this...
            await asyncio.sleep(0.7)
