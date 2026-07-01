"""
Calendar tools implementation providing event management functionality.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .data_store import get_collection

calendar_events: Optional[List[Dict]] = None
last_event_id: Optional[int] = None


def _get_calendar_events() -> List[Dict]:
    global calendar_events
    if calendar_events is None:
        calendar_events = get_collection("calendar_events")
    return calendar_events


def _parse_datetime(date: str, time: str) -> datetime:
    return datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")


def _format_event(event: Dict) -> str:
    duration = _get_event_duration_minutes(event)
    end_time = ""
    try:
        start = _parse_datetime(event["date"], event["time"])
        end = start + timedelta(minutes=duration)
        end_time = end.strftime("%H:%M")
    except Exception:
        end_time = ""
    return (
        f"Event ID: {event['id']}\n"
        f"Title: {event['title']}\n"
        f"Date: {event['date']}\n"
        f"Time: {event['time']}\n"
        f"Duration (minutes): {duration}\n"
        f"End Time: {end_time if end_time else 'Unknown'}\n"
        f"Description: {event['description']}\n"
        f"Participants: {', '.join(event['participants']) if event['participants'] else 'None'}"
    )


def _get_event_duration_minutes(event: Dict) -> int:
    duration = event.get("duration_minutes")
    if duration is not None:
        return int(duration)
    if event.get("end_time"):
        start = _parse_datetime(event["date"], event["time"])
        end = _parse_datetime(event["date"], event["end_time"])
        return int((end - start).total_seconds() // 60)
    return 60


def list_calendar_events(date: str) -> str:
    """
    List calendar events for a specific date.

    Args:
        date: Date to list events for (YYYY-MM-DD)

    Returns:
        A formatted list of matching events.
    """
    global calendar_events
    calendar_events = _get_calendar_events()

    events = [event for event in calendar_events if event.get("date") == date]
    if not events:
        return f"No events found for {date}."

    events = sorted(events, key=lambda e: e["time"])
    result = f"Found {len(events)} event(s) on {date}:\n\n"
    for event in events:
        result += _format_event(event) + "\n" + "-" * 40 + "\n\n"

    return result.rstrip()


def check_calendar_availability(date: str, time: str) -> str:
    """
    Check calendar availability at a specific date/time.

    Args:
        date: Date to check (YYYY-MM-DD)
        time: Time to check (HH:MM)

    Returns:
        Availability message.
    """
    global calendar_events
    calendar_events = _get_calendar_events()

    target_start = _parse_datetime(date, time)
    for event in calendar_events:
        if event.get("date") != date:
            continue
        start = _parse_datetime(event["date"], event["time"])
        duration = _get_event_duration_minutes(event)
        end = start + timedelta(minutes=duration)
        if start <= target_start < end:
            return f"Not available at {date} {time}."

    return f"Available at {date} {time}."


def create_calendar_event(
    title: str,
    date: str,
    time: str,
    description: str = "",
    participants: Optional[List[str]] = None
) -> str:
    """
    Create a new calendar event.

    Args:
        title: Event title
        date: Event date (YYYY-MM-DD)
        time: Event start time (HH:MM)
        description: Event description (optional)
        participants: List of attendee email addresses (optional)

    Returns:
        Confirmation message about the created event.
    """
    global calendar_events, last_event_id
    calendar_events = _get_calendar_events()

    next_id = max([event["id"] for event in calendar_events], default=0) + 1
    event = {
        "id": next_id,
        "title": title,
        "date": date,
        "time": time,
        "description": description,
        "participants": participants or [],
        "duration_minutes": 60
    }
    calendar_events.append(event)
    last_event_id = next_id

    return "Calendar event created successfully.\n\n" + _format_event(event)


def get_calendar_event(title: str) -> str:
    """
    Get a calendar event by title.

    Args:
        title: Event title to retrieve.

    Returns:
        A formatted event description.
    """
    global calendar_events, last_event_id
    calendar_events = _get_calendar_events()

    matches = [event for event in calendar_events if event.get("title") == title]
    if not matches:
        return f"No events found with title '{title}'."

    event = matches[-1]
    last_event_id = event["id"]
    return _format_event(event)


def add_calendar_participants(title: str, participants: List[str]) -> str:
    """
    Add multiple participants to the event matching the provided title.

    Args:
        title: Event title to update.
        participants: List of attendee email addresses to add.

    Returns:
        Confirmation message.
    """
    global calendar_events, last_event_id
    calendar_events = _get_calendar_events()

    event = next((event for event in calendar_events if event["title"] == title), None)
    if not event:
        return f"No events found with title '{title}'."

    event["participants"] = sorted(set(event.get("participants", []) + participants))
    return "Participants added.\n\n" + _format_event(event)


def add_calendar_participant(title: str, participant: str) -> str:
    """
    Add a single participant to the event matching the provided title.

    Args:
        title: Event title to update.
        participant: Attendee email address to add.

    Returns:
        Confirmation message.
    """
    return add_calendar_participants(title=title, participants=[participant])
