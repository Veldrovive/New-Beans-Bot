"""Holidays from checkiday.com."""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from enum import Enum

import httpx
from bs4 import BeautifulSoup
from google.genai import types

from beans_bot.services.gemini import GeminiService

logger = logging.getLogger(__name__)


class HolidayType(Enum):
    SINGLE_DAY = "Single Day Holiday"
    MULTI_DAY = "Multi-day Event"


@dataclass
class Holiday:
    type: HolidayType
    name: str
    description: str
    link: str
    day: int
    month: int

    def __str__(self):
        return f"{self.name} ({self.type.value}) - {self.description} ({self.link})"


# "Verb a(n) ..." holidays you can actually go and do, like "Draw a Bird Day".
ACTION_VERBS = [
    "draw", "create", "make", "paint", "write", "build", "design", "invent",
    "craft", "color", "tell", "do", "start", "hug", "kiss", "thank", "plant",
    "adopt", "take", "bake", "cook", "read", "sing", "pet", "visit", "learn",
    "earn", "buy", "send", "call", "watch", "play", "give", "find", "catch",
    "wear", "eat", "drink", "walk", "clean", "wash",
]  # fmt: skip
ACTION_HOLIDAY_PATTERN = re.compile(rf"\b({'|'.join(ACTION_VERBS)})\s+an?\s+", re.IGNORECASE)


def scrape_holidays(html_content: str, day: int, month: int) -> list[Holiday]:
    soup = BeautifulSoup(html_content, "html.parser")
    holidays = []

    # Single day holidays are cards with the class 'holiday'.
    for card in soup.find_all("div", class_="holiday"):
        title_tag = card.find("h2", class_="mdl-card__title-text")
        if not title_tag or not title_tag.a:
            continue
        name = title_tag.a.text.strip()
        if name == "On This Day in History":
            continue
        desc_tag = card.find("div", class_="mdl-card__supporting-text")
        holidays.append(
            Holiday(
                type=HolidayType.SINGLE_DAY,
                name=name,
                description=desc_tag.text.strip() if desc_tag else "No description available",
                link=title_tag.a["href"],
                day=day,
                month=month,
            )
        )

    # Multi-day events continuing today are list items.
    for item in soup.find_all("li", class_="mdl-list__item"):
        primary = item.find("span", class_="mdl-list__item-primary-content")
        if not primary or not primary.a:
            continue
        sub_title = primary.find("span", class_="mdl-list__item-sub-title")
        holidays.append(
            Holiday(
                type=HolidayType.MULTI_DAY,
                name=primary.a.text.strip(),
                description=sub_title.text.strip() if sub_title else "No description available",
                link=primary.a["href"],
                day=day,
                month=month,
            )
        )

    return holidays


async def get_holidays(day: int, month: int, year: int) -> list[Holiday]:
    url = f"https://www.checkiday.com/{month}/{day}/{year}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to get holidays from {url}: {response.status_code}")
    return scrape_holidays(response.text, day, month)


async def get_action_holidays(day: int, month: int, year: int) -> list[Holiday]:
    return [h for h in await get_holidays(day, month, year) if ACTION_HOLIDAY_PATTERN.search(h.name)]


_HOLIDAY_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["reason", "event_number"],
    properties={"reason": types.Schema(type=types.Type.STRING), "event_number": types.Schema(type=types.Type.INTEGER)},
)


async def ask_best_holiday(holidays: list[Holiday], gemini: GeminiService) -> tuple[str, Holiday] | None:
    """Ask Gemini for the most fun holiday. None if Gemini isn't configured or gives a bad answer."""
    holidays_list = "\n".join(f"{i + 1}: {h.name}" for i, h in enumerate(holidays))
    prompt = f"""
{holidays_list}

You are given a list of holidays that occur on a given day.
Your job is to pick the best holiday to celebrate.
We prefer lesser known and odd holidays.
"""
    answer = await gemini.generate_json(prompt, _HOLIDAY_SCHEMA)
    if answer is None:
        return None
    index = answer["event_number"] - 1
    if not 0 <= index < len(holidays):
        logger.warning(f"Gemini picked holiday {index + 1}, which is out of range")
        return None
    return answer["reason"], holidays[index]


async def pick_holiday(holidays: list[Holiday], gemini: GeminiService) -> Holiday:
    """Gemini's pick, or a random holiday if that fails."""
    try:
        answer = await ask_best_holiday(holidays, gemini)
    except Exception as e:
        logger.error(f"Failed to ask Gemini for a holiday: {e}")
        answer = None
    if answer is not None:
        reason, holiday = answer
        logger.info(f"Gemini chose the holiday {holiday.name} because {reason}")
        return holiday
    holiday = random.choice(holidays)
    logger.info(f"Randomly chose the holiday {holiday.name}")
    return holiday
