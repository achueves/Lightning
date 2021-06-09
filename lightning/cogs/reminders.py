"""
Lightning.py - A personal Discord bot
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
import logging
import textwrap
import traceback
from datetime import datetime, timedelta
from typing import Optional, Union

import asyncpg
import discord
from discord.ext.commands import clean_content

import lightning.utils.time
from lightning import LightningBot, LightningCog, LightningContext, group
from lightning.formatters import plural
from lightning.models import Timer
from lightning.utils.helpers import BetterUserObject, dm_user

log = logging.getLogger(__name__)


class Reminders(LightningCog):
    """Commands that remind you something"""

    def __init__(self, bot: LightningBot):
        super().__init__(bot)

        self.task_available = asyncio.Event(loop=bot.loop)
        self._current_task = None
        self.dispatch_jobs = self.bot.loop.create_task(self.do_jobs())

    def cog_unload(self) -> None:
        self.dispatch_jobs.cancel()

    async def get_next_timer(self) -> Optional[Timer]:
        query = """SELECT * FROM timers
                   WHERE "expiry" < (CURRENT_DATE + $1::interval)
                   ORDER BY "expiry" LIMIT 1;"""
        record = await self.bot.pool.fetchrow(query, timedelta(days=24))
        return Timer.from_record(record) if record else None

    async def short_timers(self, seconds: float, record: Timer) -> None:
        """A short loop for the bot to process small timers."""
        await asyncio.sleep(seconds)
        self.bot.dispatch(f"{record.event}_job_complete", record)

    async def execute_timer(self, record: Timer) -> None:
        self.bot.dispatch(f'{record.event}_job_complete', record)
        await self.bot.pool.execute("DELETE FROM timers WHERE id=$1;", record.id)

    async def wait_for_timers(self) -> Optional[Timer]:
        record = await self.get_next_timer()

        if not record:
            self.task_available.clear()
            self._current_task = None
            await self.task_available.wait()
            return await self.get_next_timer()

        self.task_available.set()
        return record

    async def add_job(self, event: str, created, expiry, *, force_insert=False,
                      **kwargs) -> Union[asyncpg.Record, asyncio.Task]:
        """Adds a job/pending timer to the timer system

        Parameters
        ----------
        event : str
            The name of the event to trigger.
        created : datetime.datetime
            The creation of the timer.
        expiry : datetime.datetime
            When the job should be done.
        force_insert : bool, optional
            Whether to insert into the database regardless of how long the expiry is. Defaults to False
        **kwargs
            Keyword arguments about the event
        """
        delta = (expiry - created).total_seconds()
        if delta <= 60 and force_insert is False:
            # A loop for small timers
            return self.bot.loop.create_task(self.short_timers(delta, Timer(None, event, created, expiry, kwargs)))

        if kwargs:
            query = """INSERT INTO timers (event, created, expiry, extra)
                       VALUES ($1, $2, $3, $4::jsonb)
                       RETURNING id;"""
            args = [event, created, expiry, kwargs]
        else:
            query = """INSERT INTO timers (event, created, expiry)
                       VALUES ($1, $2, $3)
                       RETURNING id;"""
            args = [event, created, expiry]

        record = await self.bot.pool.fetchval(query, *args)

        if delta <= (86400 * 24):  # 24 days
            self.task_available.set()

        if self._current_task and expiry < self._current_task.expiry:
            # Cancel the task and re-run it
            self.dispatch_jobs.cancel()
            self.dispatch_jobs = self.bot.loop.create_task(self.do_jobs())

        return record

    async def do_jobs(self) -> None:
        await self.bot.wait_until_ready()
        try:
            while not self.bot.is_closed():
                timer = self._current_task = await self.wait_for_timers()
                timestamp = datetime.utcnow()
                if timer.expiry >= timestamp:
                    tmp = (timer.expiry - timestamp).total_seconds()
                    await asyncio.sleep(tmp)
                # Dispatch the job and delete it.
                await self.execute_timer(timer)
        except asyncio.CancelledError:
            raise
        except (discord.ConnectionClosed, asyncpg.PostgresConnectionError):
            self.dispatch_jobs.cancel()
            self.dispatch_jobs = self.bot.loop.create_task(self.do_jobs())
        except Exception:
            log.error(traceback.format_exc())
            adp = discord.AsyncWebhookAdapter(self.bot.aiosession)
            webhook = discord.Webhook.from_url(self.bot.config['logging']['timer_errors'], adapter=adp)
            await webhook.execute(f"Timers has Errored!\n```{traceback.format_exc()}```")

    @group(usage="<when>", aliases=["reminder"], invoke_without_command=True)
    async def remind(self, ctx: LightningContext, *,
                     when: lightning.utils.time.UserFriendlyTime(clean_content,
                                                                 default='something')) -> None:  # noqa: F821
        """Reminds you of something after a certain date.

        The input can be any direct date (e.g. YYYY-MM-DD)
        or a human readable offset.

        Examples:
        - "{prefix}remind in 2 days do essay" (2 days)
        - "{prefix}remind 1 hour do dishes" (1 hour)
        - "{prefix}remind 60s clean" (60 seconds)

        Times are in UTC.
        """
        await self.add_job("reminder", ctx.message.created_at, when.dt, reminder_text=when.arg,
                           author=ctx.author.id, channel=ctx.channel.id, message_id=ctx.message.id)

        duration_text = lightning.utils.time.natural_timedelta(when.dt, source=ctx.message.created_at)
        await ctx.send(f"Ok {ctx.author.mention}, I'll remind you in {duration_text} about {when.arg}.")

    # remind hide/show
    async def reminder_toggler(self, ctx: LightningContext, reminder_id: int, secret: bool) -> None:
        """Marks or unmarks a reminder from the secret status"""
        query = """SELECT extra FROM timers WHERE id=$1 AND event = 'reminder'
                   AND extra ->> 'author' = $2;"""
        record = await self.bot.pool.fetchval(query, reminder_id, str(ctx.author.id))

        if not record:
            await ctx.send("Could not find a reminder with that id.")
            return

        record['secret'] = secret

        query = """UPDATE timers SET extra=$1 WHERE id=$2;"""
        await self.bot.pool.execute(query, record, reminder_id)

        if self._current_task and reminder_id == self._current_task.id:
            # It's probably better to re-run it again.
            self._current_task.extra = record

        if secret:
            await ctx.send(f"Marked {reminder_id} as secret")
        else:
            await ctx.send(f"Unmarked {reminder_id}.")

    @remind.command()
    async def hide(self, ctx: LightningContext, reminder_id: int) -> None:
        """Marks a reminder as "secret"

           A secret reminder will not show the description if you list reminders in a server.
           When it's time to remind you, the bot will DM you about your reminder."""
        await self.reminder_toggler(ctx, reminder_id, True)

    @remind.command()
    async def show(self, ctx: LightningContext, reminder_id: int) -> None:
        """Unmarks a reminder from the "secret" status."""
        await self.reminder_toggler(ctx, reminder_id, False)

    def format_list(self, records, *, guild=False) -> discord.Embed:
        embed = discord.Embed(title="Reminders", color=0xf74b06)
        for record in records:
            timed_txt = lightning.utils.time.natural_timedelta(record['expiry'], suffix=True)
            secret = record['extra'].get("secret", False)
            if guild is True and secret is True:
                text = "This reminder is explicitly marked as secret"
            else:
                text = textwrap.shorten(record['extra']['reminder_text'], width=512)

            embed.add_field(name=f"{record['id']}: In {timed_txt}", value=text, inline=False)

        return embed

    @remind.command(name='list')
    async def listreminders(self, ctx: LightningContext) -> None:
        """Lists up to 10 of your reminders

        This will only show reminders that are longer than one minute."""
        query = """SELECT id, expiry, extra
                   FROM timers
                   WHERE event = 'reminder'
                   AND extra ->> 'author' = $1
                   ORDER BY expiry
                   LIMIT 10;
                """
        records = await self.bot.pool.fetch(query, str(ctx.author.id))

        if len(records) == 0:
            await ctx.send("Seems you haven't set a reminder yet...")
            return

        embed = self.format_list(records, guild=bool(ctx.guild))
        await ctx.send(embed=embed)

    @remind.command(name='delete', aliases=['cancel'])
    async def deletereminder(self, ctx: LightningContext, *, reminder_id: int) -> None:
        """Deletes a reminder by ID.

        You can get the ID of a reminder with {prefix}remind list

        You must own the reminder to remove it"""

        query = """DELETE FROM timers
                   WHERE id = $1
                   AND event = 'reminder'
                   AND extra ->> 'author' = $2;
                """
        result = await self.bot.pool.execute(query, reminder_id, str(ctx.author.id))
        if result == "DELETE 0":
            await ctx.send("I couldn't delete a reminder with that ID!")
            return

        if self._current_task and self._current_task.id == reminder_id:
            # Matches current timer, re-run loop as it's gone
            self.dispatch_jobs.cancel()
            self.dispatch_jobs = self.bot.loop.create_task(self.do_jobs())

        await ctx.send(f"Successfully deleted reminder (ID: {reminder_id})")

    @remind.command(name='clear')
    async def clear_reminders(self, ctx: LightningContext) -> None:
        """Clears all of your reminders"""
        queryc = """SELECT COUNT(*)
                    FROM timers
                    WHERE event = 'reminder'
                    AND extra ->> 'author' = $1
                """
        count = await self.bot.pool.fetchval(queryc, str(ctx.author.id))

        if count == 0:
            await ctx.send("You don't have any reminders that I can delete")
            return

        confirm = await ctx.prompt(f"Are you sure you want to remove {plural(count):reminder}?")
        if not confirm:
            await ctx.send("Cancelled")
            return

        query = """DELETE FROM timers
                   WHERE event = 'reminder'
                   AND extra ->> 'author' = $1
                   RETURNING id;
                """
        records = await self.bot.pool.fetch(query, str(ctx.author.id))
        ids = [r['id'] for r in records]

        if self._current_task.event == 'reminder' and self._current_task.id in ids:
            # cancel task
            self.dispatch_jobs.cancel()
            self.dispatch_jobs = self.bot.loop.create_task(self.do_jobs())

        await ctx.send("Cleared all of your reminders.")

    @LightningCog.listener()
    async def on_reminder_job_complete(self, timer: Timer) -> None:
        channel = self.bot.get_channel(timer.extra['channel'])
        user = self.bot.get_user(timer.extra['author']) or BetterUserObject(id=timer.extra['author'])

        if not channel and isinstance(user, BetterUserObject):
            # rip
            return

        timed_txt = lightning.utils.time.natural_timedelta(timer.created,
                                                           source=timer.expiry,
                                                           suffix=True)
        message = f"<@!{user.id}> You asked to be reminded {timed_txt} about "\
                  f"{timer.extra['reminder_text']}"
        secret = timer.extra.pop("secret", False)

        # The reminder will be DM'd on one of the following conditions
        # 1. The channel the reminder was made in has been deleted/is not cached.
        # 2. The reminder has been explicitly marked as secret.
        if not channel or secret is True:
            await dm_user(user, message)
            return

        kwargs = {"allowed_mentions": discord.AllowedMentions(users=[user])}

        if "message_id" in timer.extra:
            if not hasattr(channel, 'guild'):
                _id = None
            else:
                _id = channel.guild.id

            ref = discord.MessageReference(message_id=timer.extra['message_id'], channel_id=channel.id, guild_id=_id,
                                           fail_if_not_exists=False)
            kwargs.update({"reference": ref})

        await channel.send(message, **kwargs)


def setup(bot: LightningBot) -> None:
    bot.add_cog(Reminders(bot))
