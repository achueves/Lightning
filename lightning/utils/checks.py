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
import functools
import inspect

import attr
from discord.ext import commands

from lightning import errors


@attr.s(slots=True, auto_attribs=True)
class Permissions:
    bot_guild_permissions: list = []
    bot_channel_permissions: list = []
    user_guild_permissions: list = []
    user_channel_permissions: list = []


def permission_check(predicate):
    def decorator(func):
        def add_permissions():
            callback = getattr(func, 'callback', func)
            if not hasattr(callback, '__lightning_permissions__'):
                callback.__lightning_permissions__ = Permissions()

            permissions = callback.__lightning_permissions__

            if hasattr(predicate, 'guild_permissions'):
                permissions.user_guild_permissions.append(predicate.guild_permissions)
            elif hasattr(predicate, 'bot_guild_permissions'):
                permissions.bot_guild_permissions.append(predicate.bot_guild_permissions)
            elif hasattr(predicate, 'channel_permissions'):
                permissions.channel_permissions.append(predicate.user_channel_permissions)
            return add_permissions

        if isinstance(func, commands.Command):
            func.checks.append(predicate)
            add_permissions()
        else:
            if not hasattr(func, '__commands_checks__'):
                func.__commands_checks__ = []

            add_permissions()

            func.__commands_checks__.append(predicate)

        return func

    if inspect.iscoroutinefunction(predicate):
        decorator.predicate = predicate
    else:
        @functools.wraps(predicate)
        async def wrapper(ctx):
            return predicate(ctx)
        decorator.predicate = wrapper

    return decorator


def is_guild(guild_id):
    def predicate(ctx):
        if not ctx.guild:
            return False
        if ctx.guild.id == guild_id:
            return True
        else:
            raise errors.LightningError("This command cannot be run in this server!")
    return commands.check(predicate)


async def is_git_whitelisted(ctx):
    if not ctx.guild:
        return False

    if await ctx.bot.is_owner(ctx.author):
        return True

    if ctx.author.id in ctx.bot.config['git']['whitelisted_users'] and \
            ctx.guild.id in ctx.bot.config['git']['whitelisted_guilds']:
        return True

    return False


def is_one_of_guilds(*guilds):
    async def predicate(ctx):
        if not ctx.guild:
            return False
        if ctx.guild.id in guilds:
            return True
    return commands.check(predicate)


def check_channel_permissions(ctx, perms):
    """A copy of discord.py's has_permissions check
    https://github.com/Rapptz/discord.py/blob/d9a8ae9c78f5ca0eef5e1f033b4151ece4ed1028/discord/ext/commands/core.py#L1533
    """
    ch = ctx.channel
    permissions = ch.permissions_for(ctx.author)
    missing = [perm for perm, value in perms.items() if getattr(permissions, perm, None) != value]

    if not missing:
        return True
    raise commands.MissingPermissions(missing)


def has_channel_permissions(**permissions):
    async def predicate(ctx):
        return check_channel_permissions(ctx, permissions)
    predicate.channel_permissions = list(permissions.keys())
    return permission_check(predicate)


async def check_guild_permissions(ctx, perms, *, check=all):
    if await ctx.bot.is_owner(ctx.author):
        return True

    if not ctx.guild:
        return False

    resolved = ctx.author.guild_permissions
    return check(getattr(resolved, name, None) == value for name, value in perms.items())


def has_guild_permissions(**permissions):
    async def pred(ctx):
        permcheck = await check_guild_permissions(ctx, permissions, check=all)
        if permcheck is False:
            raise commands.MissingPermissions(permissions)
        return permcheck
    pred.guild_permissions = list(permissions.keys())
    return permission_check(pred)


def required_cog(cog_name):
    """Check function if a cog is loaded"""
    async def predicate(ctx):
        if not ctx.bot.get_cog(cog_name):
            raise errors.CogNotAvailable(cog_name)
        return True
    return commands.check(predicate)


def bot_has_guild_permissions(**permissions):
    async def predicate(ctx):
        if not ctx.guild:
            return False

        me = ctx.me.guild_permissions
        perms: bool = all(getattr(me, name, None) == value for name, value in perms.items())

        if perms is False:
            raise commands.BotMissingPermissions(perms)

        return perms
    predicate.bot_guild_permissions = list(permissions.keys())
    return permission_check(predicate)
