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
import datetime
import io
import json
import logging
import subprocess
import typing
import warnings
from functools import wraps

import aiohttp
import asyncpg
import discord
from discord.ext import menus

from lightning import errors
from lightning.utils.emitters import WebhookEmbedEmitter

log = logging.getLogger(__name__)


async def archive_messages(channel: discord.TextChannel, limit: int, *, filename=None, reverse=False) -> discord.File:
    """Makes a txt file containing the limit of messages specified.

    Parameters
    ----------
    channel : discord.TextChannel
        The channel to archive messages for.
    limit : int
        How many messages to search for
    filename : None, Optional
        Optional file name
    reverse : bool
        Whether to reverse the messages or not. Defaults to False

    Returns
    -------
    :class:discord.File
        A .txt file containing the messages
    """
    messages = []
    async for msg in channel.history(limit=limit):
        messages.append(f"[{msg.created_at}]: {msg.author} - {msg.clean_content}")

        if msg.attachments:
            for attachment in msg.attachments:
                messages.append(f"{attachment.url}\n")
        else:
            messages.append("\n")

    if reverse:
        messages.reverse()

    text = f"Archive of {channel} (ID: {channel.id}) "\
           f"made at {datetime.datetime.utcnow()}\n\n\n{''.join(messages)}"

    _bytes = io.StringIO()
    _bytes.write(text)
    _bytes.seek(0)

    return discord.File(_bytes, filename=filename or f"message_archive_{str(channel)}.txt")


async def message_id_lookup(bot, channel_id: int, message_id: int):
    """Helper function that performs a message lookup.

    It is preferred that you handle permissions before using this function.

    If the channel isn't found, raises :class:`ChannelNotFound`.
    If some exception happens, raises :class:`LightningError`.
    If the message isn't found, returns ``None``.

    Parameters
    ----------
    bot : LightningBot
        The bot.
    channel_id : int
        The ID of channel that the message belongs to.
    message_id : int
        The ID of the message that is being looked up.

    Returns
    -------
    :class:`discord.Message`
        The message object.

    Raises
    ------
    errors.ChannelNotFound
        Raised when the channel containing the message was deleted
    errors.LightningError
        Raised when some exception happens.
    """
    channel = bot.get_channel(channel_id)
    if channel is None:
        raise errors.ChannelNotFound("Channel was deleted.")

    try:
        msg = await channel.fetch_message(message_id)
    except discord.HTTPException:
        raise errors.LightningError("Somehow failed to find that message. Try again later?")

    return msg


async def webhook_send(session: aiohttp.ClientSession, webhook_id: int, token: str, message=None,
                       **kwargs) -> typing.Optional[discord.Message]:
    """Sends a message through a webhook

    Parameters
    ----------
    session : aiohttp.ClientSession
        The session to use.
    webhook_id : int
        The webhook's ID
    token : str
        Token of the webhook
    message : None, optional
        The content of the message to send
    **kwargs
        Keyword arguments that are passed to discord.Webhook.send()

    Returns
    -------
    Optional[discord.Message]
        Returns a message object if the wait kwarg is passed
    """
    webhook = discord.Webhook.partial(webhook_id, token, session=session)
    try:
        await webhook.send(message, **kwargs)
    except discord.NotFound:
        return None


async def dm_user(user: typing.Union[discord.User, discord.Member], message: str = None, **kwargs):
    """Sends a message to a user and handles errors

    Parameters
    ----------
    user : typing.Union[discord.User, discord.Member]
        The user you are sending the message
    message : str, Optional
        The message content
    **kwargs
        Optional kwargs that are passed into `discord.User.send`

    Returns
    -------
    bool
        Whether the message was successfully sent to the user or not.
    """
    try:
        await user.send(message, **kwargs)
        return True
    except (AttributeError, discord.HTTPException, discord.Forbidden):
        return False


class Emoji:
    greentick = "<:greenTick:613702930444451880>"
    redtick = "<:redTick:613703043283681290>"
    member_leave = "<:member_leave:613363354357989376>"
    member_join = "<:member_join:613361413272109076>"
    python = "<:python:605592693267103744>"
    dpy = "<:dpy:617883851162779648>"
    postgres = "<:postgres:617886426318635015>"
    # Presence emojis
    do_not_disturb = "<:dnd:572962188134842389>"
    online = "<:online:572962188114001921>"
    idle = "<:idle:572962188201820200>"
    offline = "<:offline:572962188008882178>"
    numbers = ('1\N{combining enclosing keycap}',
               '2\N{combining enclosing keycap}',
               '3\N{combining enclosing keycap}',
               '4\N{combining enclosing keycap}',
               '5\N{combining enclosing keycap}',
               '6\N{combining enclosing keycap}',
               '7\N{combining enclosing keycap}',
               '8\N{combining enclosing keycap}',
               '9\N{combining enclosing keycap}',
               '\N{KEYCAP TEN}')


def ticker(boolean: bool) -> str:
    if boolean:
        return Emoji.greentick
    else:
        return Emoji.redtick


class BetterUserObject(discord.Object):
    def __init__(self, id):
        super().__init__(id)

    @property
    def mention(self):
        return '<@!%s>' % self.id

    def __str__(self):
        return str(self.id)


class ConfirmationMenu(menus.Menu):
    """A confirmation menu.

    Parameters
    ----------
    ctx : Context
        The context of the command.
    message : str
        The message to send with the menu.
    timeout : float
        How long to wait for a response before returning.
    delete_message_after : bool
        Whether to delete the message after an option has been selected.
    confirmation_message : bool
        Whether to use the default confirmation message or not.

    Returns
    -------
    Optional[bool]
        ``True`` if explicit confirm,
        ``False`` if explicit deny,
        ``None`` if deny due to timeout
    """

    def __init__(self, ctx, message, *, timeout=30.0, delete_message_after=False, confirmation_message=True):
        super().__init__(timeout=timeout, delete_message_after=delete_message_after)
        self.ctx = ctx
        self.result = None

        if ctx.guild is not None:
            self.permissions = ctx.channel.permissions_for(ctx.guild.me)
        else:
            self.permissions = ctx.channel.permissions_for(ctx.bot.user)

        if not self.permissions.external_emojis:
            # Clear buttons and fallback to the Unicode emojis
            self.clear_buttons()
            confirm = menus.Button("\N{WHITE HEAVY CHECK MARK}", self.do_confirm)
            deny = menus.Button("\N{CROSS MARK}", self.do_deny)
            self.add_button(confirm)
            self.add_button(deny)

        if confirmation_message is True:
            reactbuttons = list(self.buttons.keys())
            self.msg = f"{message}\n\nReact with {reactbuttons[0]} to confirm or"\
                       f" {reactbuttons[1]} to deny."
        else:
            self.msg = message

    async def send_initial_message(self, ctx, channel) -> discord.Message:
        return await channel.send(self.msg)

    @menus.button(Emoji.greentick)
    async def do_confirm(self, payload) -> None:
        self.result = True
        self.stop()

    @menus.button(Emoji.redtick)
    async def do_deny(self, payload) -> None:
        self.result = False
        self.stop()

    async def prompt(self) -> bool:
        await self.start(self.ctx, wait=True)
        return self.result


async def request(url, session: aiohttp.ClientSession, *, timeout=180, method: str = "GET", return_text=False,
                  **kwargs) -> typing.Union[dict, str, bytes]:
    async with session.request(method, url, timeout=timeout, **kwargs) as resp:
        if resp.status == 429:
            log.info(f"Ratelimited while requesting {url}")
            raise errors.HTTPRatelimited(resp)

        # TODO: Make it better
        if resp.status == 404:
            log.info(f"404 while requesting {url}")
            raise errors.HTTPException(resp)

        if 300 > resp.status >= 200:
            if return_text is True:
                return await resp.text()

            try:
                return await resp.json()
            except aiohttp.ContentTypeError:
                return await resp.read()
        else:
            raise errors.HTTPException(resp)


async def haste(session: aiohttp.ClientSession, text: str, instance: str = 'https://mystb.in/') -> str:
    """Posts to a haste instance and returns the link.

    Parameters
    ----------
    session : aiohttp.ClientSession
    text : str
        The text to post to the instance.
    instance : str
        Link to a haste instance. By default https://mystb.in/ is used.

    Returns
    -------
    str
        A link to the created haste"""
    resp = await request(f"{instance}documents", session, method="POST", data=text)
    return f"{instance}{resp['key']}"


async def run_in_shell(command: str):
    try:
        pipe = asyncio.subprocess.PIPE
        process = await asyncio.create_subprocess_shell(command,
                                                        stdout=pipe,
                                                        stderr=pipe)
        stdout, stderr = await process.communicate()
    except NotImplementedError:
        process = subprocess.Popen(command, shell=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
    return stdout.decode('utf-8'), stderr.decode('utf-8')


async def create_pool(dsn: str, **kwargs) -> asyncpg.Pool:
    """Creates a connection pool with type codecs for json and jsonb"""

    async def init(connection: asyncpg.Connection):
        await connection.set_type_codec('json', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
        await connection.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')

    return await asyncpg.create_pool(dsn, init=init, **kwargs)


def deprecated(deprecated_in: str = None, removed_in: str = None, details: str = None):
    """A decorator that marks a function as deprecated"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            dfmt = f" as of {deprecated_in}" if deprecated_in else ""
            rfmt = f" and will be removed in {removed_in}" if removed_in else ""

            defmt = "" if not details else details
            fmt = f"{func.__name__} is deprecated{dfmt}{rfmt}. {defmt}"

            warnings.simplefilter("always", DeprecationWarning)
            warnings.warn(fmt, category=DeprecationWarning, stacklevel=2)
            warnings.simplefilter("default", DeprecationWarning)
            return func(*args, **kwargs)
        return wrapper
    return decorator


@deprecated("3.2.0", "4.0.0", "Use lightning.utils.emitters.WebhookEmbedEmitter instead")
class WebhookEmbedBulker(WebhookEmbedEmitter):
    ...


async def safe_delete(message) -> bool:
    """Helper function to safely delete a message.

    This is just a try/except.

    Returns
    -------
    bool
        An indicator whether the message was successfully deleted or not."""
    try:
        await message.delete()
        return True
    except discord.HTTPException:
        return False
