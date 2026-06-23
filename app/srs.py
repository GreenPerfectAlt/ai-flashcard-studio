from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math


@dataclass(frozen=True)
class ReviewState:
    ease_factor: float = 2.5
    interval_days: int = 0
    review_count: int = 0
    lapses: int = 0


@dataclass(frozen=True)
class ReviewResult:
    ease_factor: float
    interval_days: int
    review_count: int
    lapses: int
    due_date: datetime
    status: str
    last_reviewed_at: datetime


def start_of_local_day(days: int = 0) -> datetime:
    today = date.today() + timedelta(days=max(0, int(days or 0)))
    return datetime(today.year, today.month, today.day)


def schedule_review(state: ReviewState, rating: str, now: datetime | None = None) -> ReviewResult:
    rating = (rating or "good").lower()
    if rating == "medium":
        rating = "good"

    reviewed_at = now or datetime.now()
    ease = float(state.ease_factor or 2.5)
    interval = int(state.interval_days or 0)
    reps = int(state.review_count or 0) + 1
    lapses = int(state.lapses or 0)

    if rating == "again":
        lapses += 1
        interval = 0
        ease = max(1.3, ease - 0.2)
        status = "today"
    elif rating == "hard":
        interval = max(1, math.ceil(max(1, interval) * 1.25))
        ease = max(1.3, ease - 0.08)
        status = "planned"
    elif rating == "easy":
        interval = 4 if reps <= 1 else max(4, math.ceil(max(1, interval) * (ease + 0.35)))
        ease = min(3.2, ease + 0.15)
        status = "planned"
    else:
        if reps <= 1:
            interval = 1
        elif reps == 2:
            interval = max(3, interval)
        else:
            interval = max(2, math.ceil(max(1, interval) * ease))
        status = "planned"

    due_date = start_of_local_day(interval)
    if rating == "easy" and interval >= 14:
        status = "done"

    return ReviewResult(
        ease_factor=round(ease, 2),
        interval_days=int(interval),
        review_count=reps,
        lapses=lapses,
        due_date=due_date,
        status=status,
        last_reviewed_at=reviewed_at,
    )
