"""
Pydantic schema for the bot config.

Hydra composes the YAML in ./configs, then scripts/run_bot.py validates the
resolved result with BotConfig. Names follow the Discord API docs: what the
Discord UI calls a "server" is a "guild" here.
"""

from __future__ import annotations

import datetime
import zoneinfo
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SecretStr, field_validator, model_validator

from beans_bot.currency import parse_amount

# A coin amount written in the config. Bare numbers are BeanCoins ("10" = 10 BeanCoins),
# or a unit can be given ("5 cheddar"). Stored as base units.
CoinAmount = Annotated[int, BeforeValidator(lambda v: parse_amount(v))]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Global ---------------------------------------------------------------------------


class GeminiConfig(StrictModel):
    api_key: SecretStr | None = None
    model: str | None = None
    thinking_level: str | None = None
    # When true the bot refuses to start if Gemini does not answer a test prompt.
    required: bool = True

    @property
    def enabled(self) -> bool:
        return self.api_key is not None and bool(self.api_key.get_secret_value()) and self.model is not None


class ClassifierGlobalConfig(StrictModel):
    enabled: bool = True
    models_dir: Path = Path("models/classifier")
    device: str = "cpu"
    torch_cpu_threads: int = 1
    hf_token: SecretStr | None = None


class CurrencyConfig(StrictModel):
    cheddar_enabled: bool = False


# --- Per guild ------------------------------------------------------------------------


class BribeConfig(StrictModel):
    cost: CoinAmount
    success_scripts: list[str] = []  # {bot_name}, {jailed_user}
    failure_scripts: list[str] = []  # {bot_name}, {jailed_user}


class FrameConfig(StrictModel):
    """Paying the bot to jail someone else. The anonymous price keeps the framer's name out of the announcement."""

    cost: CoinAmount = parse_amount(3)
    anonymous_cost: CoinAmount = parse_amount(6)
    public_scripts: list[str] = []  # {framer}, {jailed_user}, {paid}
    anonymous_scripts: list[str] = []  # {jailed_user}, {paid}
    # Used instead of the above when someone frames the bot itself.
    bot_public_scripts: list[str] = []  # {framer}, {bot_name}, {paid}
    bot_anonymous_scripts: list[str] = []  # {bot_name}, {paid}


class JailConfig(StrictModel):
    jail_emoji: str
    tomato_emoji: str
    to_jail_threshold: int
    mega_jail_threshold: int
    jail_length: datetime.timedelta  # Hydra/YAML: seconds, or ISO 8601 like "PT24H"
    jailed_role_id: int
    subsequent_tomato_probability: float = 0.1
    desync_release_probability: float = 0.5

    on_jailed_scripts: list[str] = []  # {jailed_user}
    mega_jail_scripts: list[str] = []  # {jailed_user}
    humiliate_scripts: list[str] = []  # {jailed_user}
    assault_innocent_scripts: list[str] = []  # {reacting_user}, {attacked_user}
    already_in_jail_scripts: list[str] = []  # {jailed_user}
    release_scripts: list[str] = []  # {jailed_user}
    scot_free_scripts: list[str] = []  # {jailed_user}: had the role but no record, set free
    forgotten_prisoner_scripts: list[str] = []  # {jailed_user}: had the role but no record, re-jailed

    bribe: BribeConfig | None = None
    frame: FrameConfig | None = None


class CoinsConfig(StrictModel):
    emoji: str
    reaction_value: CoinAmount = parse_amount(1)
    max_reactions_per_day: int
    max_message_age: datetime.timedelta = datetime.timedelta(days=3)


class BotBettingConfig(StrictModel):
    enabled: bool = False
    bet_probability: float = 0.5
    bet_proportion: float = 0.1
    max_bet: CoinAmount = parse_amount(50)
    min_bet_range: tuple[CoinAmount, CoinAmount] = (parse_amount(1), parse_amount(4))


class GamblingConfig(StrictModel):
    forum_channel_id: int
    bot_betting: BotBettingConfig = BotBettingConfig()
    # Sent to the bot channel with an @everyone when someone swaps their bet, which also jails them.
    swap_scripts: list[str] = []  # {member}, {old_choice}, {new_choice}, {amount}, {market}
    # Winners who only beat the house, this soon after the market opened, are refunded and jailed.
    rigged_window: datetime.timedelta = datetime.timedelta(hours=2)
    rigged_scripts: list[str] = []  # {members}, {amount}


class BirthdayConfig(StrictModel):
    # The message that lists everyone's birthdays, one per line as "- Name: Month Day[, Year]".
    birthdays_message_channel_id: int
    birthdays_message_id: int
    enable_holidays: bool = True
    enable_on_this_day: bool = True


class ActionHolidayConfig(StrictModel):
    pass


class FatCowReactions(StrictModel):
    # Each emoji can be a comma-separated list. Entries can be custom guild emoji names,
    # :alias: shortcodes, or unicode emoji.
    no_fat_cow_emoji: str = ""
    low_fat_cow_threshold: float = 0.5
    low_fat_cow_emoji: str = "cow"
    medium_fat_cow_threshold: float = 1.0
    medium_fat_cow_emoji: str = ""
    high_fat_cow_threshold: float = 1.5
    high_fat_cow_emoji: str = ""
    very_high_fat_cow_emoji: str = ""


class FatCowConfig(StrictModel):
    reactions: FatCowReactions = FatCowReactions()


class ClassifierScript(StrictModel):
    priority: int
    label: str  # A model label, or "ANY"
    confidence: str  # A key of confidence_thresholds
    text: str  # May use {<model name>_label_<rank>}


class ClassifierModelConfig(StrictModel):
    model_path: str  # Relative to classifier.models_dir
    auto_respond_channels: list[int] = []
    manual_trigger_reaction: str | None = None
    confidence_thresholds: dict[str, float]
    select_random_prob: float = 0.0  # Otherwise pick the least recently used script
    scripts: list[ClassifierScript]


class ClassifierGuildConfig(StrictModel):
    models: dict[str, ClassifierModelConfig]


class RoleNameConfig(StrictModel):
    student_role_id: int
    required_votes: int = 1
    allow_self_voting: bool = True


class DayPassConfig(StrictModel):
    role_id: int
    cost: CoinAmount = parse_amount(2)
    length: datetime.timedelta = datetime.timedelta(days=1)  # Hydra/YAML: seconds, or ISO 8601 like "PT24H"
    purchase_scripts: list[str] = []  # {buyer}
    expire_scripts: list[str] = []  # {member}


class OnePercentConfig(StrictModel):
    role_id: int
    starting_price: CoinAmount = parse_amount(10)  # The price before anyone has claimed it
    price_increment: CoinAmount = parse_amount(1)  # The next price is what the holder paid plus this
    first_claim_scripts: list[str] = []  # {buyer}, {paid}, {new_price}
    usurp_scripts: list[str] = []  # {buyer}, {previous_holder}, {paid}, {new_price}


class HenchmanConfig(StrictModel):
    role_id: int  # Give this role access to the VIP channel in Discord too
    appoint_scripts: list[str] = []  # {boss}, {henchman}
    dismiss_scripts: list[str] = []  # {boss}, {henchman}
    unemployed_scripts: list[str] = []  # {henchman}, {previous_holder}, {buyer}. The 1% changed hands.


class VipConfig(StrictModel):
    """Roles that see the VIP channel (set up in Discord): a day pass, one permanent title, and its henchman."""

    day_pass: DayPassConfig
    one_percent: OnePercentConfig
    henchman: HenchmanConfig | None = None  # Missing means the 1% can't appoint one


class CouncilMemberConfig(StrictModel):
    internal_name: str
    ted_id: int
    council_name: str  # "The Second True Ted"
    epithets: list[str] = []
    words: str = ""  # House words, like "Dempseram"
    user_id: int
    crest_emoji: str  # Only this member may react with it


class CouncilConfig(StrictModel):
    """The Council of Teds. Any feature can ask it for a vote. Each member votes with their own crest emoji."""

    nay_vote_emoji: str | None = None
    members: dict[str, CouncilMemberConfig]


class SnipesConfig(StrictModel):
    """
    Photos of members posted in the snipe channel. The Council of Teds verifies them with crest
    reactions, or rejects them with its nay emoji.
    """

    channel_id: int
    pending_emoji: str = "⏳"
    verified_emoji: str = "✅"
    rejected_emoji: str = "❌"
    expired_emoji: str = "💤"
    # How long the poster has to say who's in the photo when they tagged nobody (or too many people).
    picker_timeout: datetime.timedelta = datetime.timedelta(minutes=5)  # Hydra/YAML: seconds, or ISO 8601
    # How long the council has to decide before the snipe is thrown out.
    vote_timeout: datetime.timedelta = datetime.timedelta(days=2)  # Hydra/YAML: seconds, or ISO 8601
    picker_scripts: list[str] = []  # {sniper}
    # Pings the council. Deleted and replaced by one of the scripts below once the snipe is decided.
    alert_scripts: list[str] = []  # {council}, {sniper}, {target}
    verified_scripts: list[str] = []  # {sniper}, {target}, {verifiers}
    rejected_scripts: list[str] = []  # {sniper}, {target}, {rejecters}
    expired_scripts: list[str] = []  # {sniper}, {target}


class ManifestoConfig(StrictModel):
    """Members' manifestos. The bot only ever quotes the wisdom pulled out of them, never the manifesto itself."""

    # Discord user ID -> folder under manifestos_dir holding that member's wisdom.json
    authors: dict[int, str]
    intro_scripts: list[str] = []  # {author}
    # Used instead of intro_scripts when wisdom is imparted as a reply to someone's message
    reply_scripts: list[str] = []  # {imparter}, {author}, {target}
    # Posted with the new wisdom when a member adds to their manifesto
    added_scripts: list[str] = []  # {author}


class GuildConfig(StrictModel):
    name: str
    active: bool = True
    bot_channel_id: int | None = None

    jail: JailConfig | None = None
    coins: CoinsConfig | None = None
    gambling: GamblingConfig | None = None
    birthday: BirthdayConfig | None = None
    action_holiday: ActionHolidayConfig | None = None
    fat_cow: FatCowConfig | None = None
    classifier: ClassifierGuildConfig | None = None
    role_name: RoleNameConfig | None = None
    vip: VipConfig | None = None
    council_of_teds: CouncilConfig | None = None
    snipes: SnipesConfig | None = None
    manifesto: ManifestoConfig | None = None

    @model_validator(mode="after")
    def _snipes_need_a_council(self) -> GuildConfig:
        if self.snipes is not None and self.council_of_teds is None:
            raise ValueError("snipes needs a council_of_teds section to verify them")
        return self


# --- Root -----------------------------------------------------------------------------


class BotConfig(StrictModel):
    bot_token: SecretStr
    db_path: Path = Path("data/beans.db")
    manifestos_dir: Path = Path("manifestos")
    timezone: str = "America/Detroit"
    daily_post_time: datetime.time = datetime.time(hour=10)
    sync_commands_on_startup: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    currency: CurrencyConfig = CurrencyConfig()
    gemini: GeminiConfig = GeminiConfig()
    classifier: ClassifierGlobalConfig = ClassifierGlobalConfig()

    guilds: dict[int, GuildConfig] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        zoneinfo.ZoneInfo(v)
        return v

    @property
    def tz(self) -> zoneinfo.ZoneInfo:
        return zoneinfo.ZoneInfo(self.timezone)

    @property
    def daily_post_time_tz(self) -> datetime.time:
        return self.daily_post_time.replace(tzinfo=self.tz)

    def guild(self, guild_id: int | None) -> GuildConfig | None:
        """Config for an active guild, or None if the guild is unknown or inactive."""
        if guild_id is None:
            return None
        config = self.guilds.get(guild_id)
        if config is None or not config.active:
            return None
        return config
