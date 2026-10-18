"""Historical events from onthisday.com."""

from __future__ import annotations

import datetime
import logging
import random
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from google.genai import types

from beans_bot.services.gemini import GeminiService

logger = logging.getLogger(__name__)


@dataclass
class OnThisDayEvent:
    day: int
    month: int
    year: int
    description: str
    link: str

    def __str__(self):
        return f"**{self.year}**: {self.description} ({self.link})"


def scrape_on_this_day_events(html_content: str, day: int, month: int) -> list[OnThisDayEvent]:
    soup = BeautifulSoup(html_content, "html.parser")
    events = []

    # Only the highlighted sections, not the plain <ul class="event-list">.
    for section in soup.select(".section--highlight"):
        title_tag = section.select_one(".poi__heading-txt")
        p_tag = section.select_one("p")
        if not title_tag or not p_tag:
            continue
        date_tag = p_tag.select_one(".date")
        if not date_tag:
            continue
        try:
            year = int(date_tag.get_text(strip=True))
        except ValueError:
            year = datetime.datetime.now().year
        # Remove the date so it isn't part of the description.
        date_tag.extract()
        description = f"{title_tag.get_text(strip=True)}: {p_tag.get_text(separator=' ', strip=True).lstrip(': ')}"

        # Prefer the article link in the heading, then the first link in the description.
        link_tag = section.select_one(".poi__heading a.section__link") or p_tag.select_one("a")
        link = str(link_tag.get("href", "")) if link_tag else ""
        if link.startswith("/"):
            link = f"https://www.onthisday.com{link}"

        events.append(OnThisDayEvent(day=day, month=month, year=year, description=description, link=link))

    return events


async def get_on_this_day(day: int, month: int) -> list[OnThisDayEvent]:
    month_name = datetime.date(2000, month, 1).strftime("%B").lower()
    url = f"https://www.onthisday.com/events/{month_name}/{day}"
    # The site blocks default Python user agents.
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to get on this day from {url}: {response.status_code}")
    return scrape_on_this_day_events(response.text, day, month)


_EVENT_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["reason", "on_this_day_number"],
    properties={"reason": types.Schema(type=types.Type.STRING), "on_this_day_number": types.Schema(type=types.Type.INTEGER)},
)


async def ask_best_event(events: list[OnThisDayEvent], gemini: GeminiService) -> tuple[str, OnThisDayEvent] | None:
    events_list = "\n".join(f"{i + 1}: {e.year} - {e.description}" for i, e in enumerate(events))
    prompt = f"""
{events_list}

You are given a list of events that happened on a given day.
Your job is to pick the best event to celebrate.
We prefer lesser known and odd events that are still important to history.
"""
    answer = await gemini.generate_json(prompt, _EVENT_SCHEMA)
    if answer is None:
        return None
    index = answer["on_this_day_number"] - 1
    if not 0 <= index < len(events):
        logger.warning(f"Gemini picked event {index + 1}, which is out of range")
        return None
    return answer["reason"], events[index]


async def pick_event(events: list[OnThisDayEvent], gemini: GeminiService) -> OnThisDayEvent:
    """Gemini's pick, or a random event if that fails."""
    try:
        answer = await ask_best_event(events, gemini)
    except Exception as e:
        logger.error(f"Failed to ask Gemini for an on this day event: {e}")
        answer = None
    if answer is not None:
        reason, event = answer
        logger.info(f"Gemini chose the on this day event {event.description} because {reason}")
        return event
    event = random.choice(events)
    logger.info(f"Randomly chose the on this day event {event.description}")
    return event
