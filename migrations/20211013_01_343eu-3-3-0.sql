-- 3.3.0
-- depends: 20210622_01_U0aRl-3-2-0

CREATE TABLE IF NOT EXISTS automod
(
    guild_id BIGINT NOT NULL REFERENCES guilds (id) ON DELETE CASCADE PRIMARY KEY,
    config TEXT
);