from enum import IntEnum
from typing import Optional, Union

import discord
from tomlkit import loads as toml_loads
from tomlkit.items import Integer, String, Table

# This might be an enum instead
VALID_AUTOMOD_TYPES = ["message-spam", "mass-mentions"]


class ConfigurationError(Exception):
    ...


class AutomodPunishmentEnum(IntEnum):
    WARN = 1
    MUTE = 2
    KICK = 3
    BAN = 4


def convert_to_automod_punishment(value: str):
    try:
        return AutomodPunishmentEnum(value)
    except Exception:
        raise ConfigurationError("Invalid automod punishment supplied")


class AutomodPunishmentConfig:
    def __init__(self, type) -> None:
        self.type = convert_to_automod_punishment(type)
        # self.max: int = ...
        self.seconds: Optional[Union[float, int]] = ...


class BaseConfig:
    def __init__(self, type, per, punishment_cfg) -> None:
        if type not in VALID_AUTOMOD_TYPES:
            raise ConfigurationError("Invalid automod type")

        self.type = type
        self.per = per
        self.punishment = AutomodPunishmentConfig(punishment_cfg)


class MessageSpamConfig(BaseConfig):
    def __init__(self, type: str, per, sec, punishment_config: Optional[dict]) -> None:
        super().__init__(type, per, punishment_config)
        self.seconds = sec


def parse_config(key: str, value):
    if key not in VALID_AUTOMOD_TYPES:
        raise ConfigurationError("Invalid automod type")

    per = value.get("per", None)
    sec = value.get("seconds", None)
    if per is None and sec is None:
        raise ConfigurationError("Missing one of \"per\" or \"seconds\".")

    # Other configuration parameters may need to be validated...

    punishment = value.get("punishment", None)

    if punishment and type(punishment) not in (String, Integer):  # at some point we'll support String
        # Optional link to configuration docs
        raise ConfigurationError("Punishment should be an integer, not a subtable.")

    if key == "mass-mentions":
        return BaseConfig(key, per, punishment)

    try:
        return MessageSpamConfig(key, per, sec, punishment)
    except Exception as e:
        raise ConfigurationError(e)


async def from_file(file: discord.Attachment):
    file = await file.read()
    x = toml_loads(file)
    cfgs = []

    for key, value in list(x.items()):
        if key == "automod":
            if type(value) != Table:
                raise ConfigurationError(f"Expected a subtable, got {value.__class__.__name__} instead.")

            for key, value in list(value.items()):
                cfgs.append(parse_config(key, value))

    return cfgs
