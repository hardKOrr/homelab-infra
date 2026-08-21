#!/usr/bin/env python3
"""Resolve a maintenance schedule and answer the two questions the platform asks of it.

Reads a JSON request on stdin and writes a JSON answer on stdout:

    {"mode": "window", "due": true, "cron": "0 0 3 * * 0", "text": "Sun 03:00 +120m", ...}

Request keys:

    chains    required -- a list of chains. One chain is an ordered list of layers,
              lowest precedence first: global, estate, stack, app. Each layer is
              {"name": str, "schedule": <declaration or null>}; a null or absent
              declaration inherits, and a declaration REPLACES the inherited one
              whole rather than merging into it.
              One chain resolves that chain. Several chains resolve each and then
              intersect them -- the shared-host rule, where a guest carrying several
              apps may only be disrupted inside every one of their windows.
    now       optional -- "YYYY-MM-DDTHH:MM" on the lab's own clock. Testing supplies
              it; a real run leaves it out and the current time is used.
    timezone  optional -- the lab's timezone (homelabinfra_config.timezone), used to
              read the current time when `now` is absent. A window is declared on the
              lab's clock, not the runner's, and the runner is not guaranteed to keep
              the same one -- a runner left on UTC would open a 03:00 window at what
              the operator experiences as 21:00. Absent or unknown falls back to the
              runner's local time, which is the previous behaviour and correct
              whenever the two agree.

A schedule declaration is one of:

    "always"                                  disrupt whenever something needs it
    "never"                                   never disrupt; notify only
    {"days": ["Sun"], "start": "03:00", "duration": 120}
    {"days": "any", "start": "03:00", "duration": 120}

Why a script and not Jinja: the whole point of the primitive is that several
declarations can meet on one guest, and the answer to "may this guest reboot now" is
then the INTERSECTION of their windows. Intersecting day sets and clock ranges that
may wrap past midnight is set arithmetic, and a template that got it subtly wrong
would reboot a service outside the window its owner declared -- the one failure this
slice exists to prevent.

An empty intersection is reported as mode "never" with a `conflict` naming the
declarations that cannot both be honoured. It is never resolved by picking a winner:
the operator declared two incompatible windows on one host, and the platform must say
so rather than disrupt an app outside the window it was promised.

Everything is computed over minutes-of-week (0 = Monday 00:00, 10080 in a week), so
midnight wrap, multi-day windows and intersection are one mechanism with no cases.
"""

import datetime
import json
import sys

WEEK = 7 * 24 * 60
DAY = 24 * 60
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Index is Python's weekday(): Monday 0.
DAY_ALIASES = {name.lower(): index for index, name in enumerate(DAY_NAMES)}
DAY_ALIASES.update({
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
})

# robfig/cron (which Watchtower reads) numbers day-of-week from 0 = Sunday;
# Python numbers it from 0 = Monday.
CRON_DOW = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}

# What "always" means to a container updater. Nothing constrains the restart, so poll
# often enough to be useful and rarely enough to stay off a registry's rate limit.
ALWAYS_CRON = "0 0 * * * *"


class ScheduleError(Exception):
    """A declaration that cannot be understood, with an operator-readable reason."""


def parse_clock(text, label):
    """Turn "HH:MM" into minutes past midnight."""
    text = str(text).strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ScheduleError("%s %r is not a HH:MM time" % (label, text))
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ScheduleError("%s %r is not a HH:MM time" % (label, text))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError("%s %r is not a time of day" % (label, text))
    return hour * 60 + minute


def parse_days(value, label):
    """Turn a day list (or "any"/"daily") into a sorted list of weekday indexes."""
    if value is None:
        return list(range(7))
    if isinstance(value, str):
        if value.strip().lower() in ("any", "daily", "all", "*"):
            return list(range(7))
        value = [value]
    if not isinstance(value, list) or not value:
        raise ScheduleError("%s must be 'any' or a non-empty list of day names" % label)
    days = []
    for entry in value:
        key = str(entry).strip().lower()
        if key not in DAY_ALIASES:
            raise ScheduleError(
                "%s does not know the day %r; use one of %s, or 'any'"
                % (label, entry, ", ".join(DAY_NAMES))
            )
        days.append(DAY_ALIASES[key])
    return sorted(set(days))


def minutes_of(declaration, label):
    """Expand one declaration into (mode, set of minutes-of-week).

    "always" is the full week and "never" is the empty set, so all three forms are one
    object downstream and only the reported mode tells them apart.
    """
    if declaration is None:
        raise ScheduleError("%s is empty" % label)

    if isinstance(declaration, str):
        word = declaration.strip().lower()
        if word == "always":
            return "always", set(range(WEEK))
        if word == "never":
            return "never", set()
        raise ScheduleError(
            "%s %r is not 'always', 'never', or a window {days, start, duration}"
            % (label, declaration)
        )

    if not isinstance(declaration, dict):
        raise ScheduleError("%s must be 'always', 'never', or a window mapping" % label)

    mode = str(declaration.get("mode", "")).strip().lower()
    if mode in ("always", "never"):
        return minutes_of(mode, label)

    if "start" not in declaration:
        raise ScheduleError("%s declares a window without a `start` time" % label)
    if "duration" not in declaration:
        raise ScheduleError(
            "%s declares a window without a `duration` in minutes" % label
        )
    try:
        duration = int(declaration["duration"])
    except (TypeError, ValueError):
        raise ScheduleError(
            "%s duration %r is not a number of minutes" % (label, declaration["duration"])
        )
    if duration <= 0:
        raise ScheduleError("%s duration must be at least 1 minute" % label)
    if duration > WEEK:
        raise ScheduleError("%s duration %d is longer than a week" % (label, duration))

    start = parse_clock(declaration["start"], "%s start" % label)
    days = parse_days(declaration.get("days"), "%s days" % label)

    minutes = set()
    for day in days:
        origin = day * DAY + start
        for step in range(duration):
            minutes.add((origin + step) % WEEK)
    return "window", minutes


def resolve_chain(chain, index):
    """Walk one chain from lowest precedence to highest and return the winner."""
    if not isinstance(chain, list) or not chain:
        raise ScheduleError(
            "chain %d is empty; declare at least a global layer" % index
        )
    winner = None
    source = None
    for layer in chain:
        if not isinstance(layer, dict):
            raise ScheduleError("chain %d has a layer that is not a mapping" % index)
        declaration = layer.get("schedule")
        if declaration is None or declaration == "":
            continue
        winner = declaration
        source = layer.get("name") or "(unnamed layer)"
    if winner is None:
        raise ScheduleError(
            "chain %d declares no schedule at any layer; config/infrastructure.yml "
            "must carry a maintenance.schedule default" % index
        )
    return winner, source


def runs_of(minutes):
    """Split a minute set into contiguous (start, length) runs.

    A window that wraps Sunday midnight arrives as a run ending at 10079 and another
    beginning at 0. They are one window and are reported as one.
    """
    if not minutes or len(minutes) == WEEK:
        return []
    ordered = sorted(minutes)
    runs = []
    start = previous = ordered[0]
    for minute in ordered[1:]:
        if minute != previous + 1:
            runs.append((start, previous - start + 1))
            start = minute
        previous = minute
    runs.append((start, previous - start + 1))

    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][0] + runs[-1][1] == WEEK:
        head = runs.pop(0)
        tail = runs.pop()
        runs.append((tail[0], tail[1] + head[1]))
    return sorted(runs)


def clock_of(minute):
    """Turn a minute-of-week into (day name, hour, minute)."""
    minute %= WEEK
    return DAY_NAMES[minute // DAY], (minute % DAY) // 60, minute % 60


def describe(mode, minutes):
    """The window in words, for the job log and the notification.

    Runs that share an opening time and a length are collapsed onto one line: a daily
    window is one window an operator reads at a glance, not seven identical clauses.
    """
    if mode == "always":
        return "always (disrupt whenever it is needed)"
    if mode == "never":
        return "never (notify only)"

    grouped = {}
    order = []
    for start, length in runs_of(minutes):
        day, hour, minute = clock_of(start)
        key = (hour, minute, length)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(day)

    parts = []
    for hour, minute, length in order:
        days = grouped[(hour, minute, length)]
        label = "daily" if len(days) == 7 else ",".join(days)
        parts.append("%s %02d:%02d +%dm" % (label, hour, minute, length))
    return ", ".join(parts) or "never (notify only)"


def cron_of(mode, minutes):
    """A 6-field cron firing when the window opens. Watchtower reads this."""
    if mode == "never":
        return None, None
    if mode == "always":
        return ALWAYS_CRON, None
    runs = runs_of(minutes)
    if not runs:
        return None, None
    opens = [clock_of(start) for start, _ in runs]
    times = set((hour, minute) for _, hour, minute in opens)
    days = sorted(set(DAY_NAMES.index(day) for day, _, _ in opens))
    if len(times) == 1:
        hour, minute = times.pop()
        dow = "*" if len(days) == 7 else ",".join(str(CRON_DOW[d]) for d in days)
        return "0 %d %d * * %s" % (minute, hour, dow), None
    # Several openings at different clock times cannot be one cron expression. Take the
    # first and say so, rather than inventing a schedule nobody declared.
    day, hour, minute = opens[0]
    return (
        "0 %d %d * * %d" % (minute, hour, CRON_DOW[DAY_NAMES.index(day)]),
        "the window opens at different times on different days; the container restart "
        "uses the first opening only",
    )


def next_open(mode, minutes, now):
    """When the window next opens, at or after `now`, as an ISO timestamp.

    Tier 2 needs this and `due` cannot supply it: arming a descent means writing an
    absolute wall-clock time into a systemd timer on a machine that will execute it
    with no control plane left to correct it, so the platform has to name the moment
    rather than keep asking "is it time yet".

    An OPENING is a minute inside the window whose predecessor is outside it, so a
    window already in progress reports its NEXT opening rather than pretending to open
    again mid-flight. `always` has no opening -- it is open now. `never` has none at all.
    """
    if mode == "never" or not minutes:
        return None
    if mode == "always":
        return now.replace(second=0, microsecond=0).isoformat()
    here = now.weekday() * DAY + now.hour * 60 + now.minute
    for step in range(1, WEEK + 1):
        minute = (here + step) % WEEK
        if minute in minutes and (minute - 1) % WEEK not in minutes:
            moment = now.replace(second=0, microsecond=0) + datetime.timedelta(minutes=step)
            return moment.isoformat()
    return None


def now_in(timezone):
    """The current wall-clock time on the lab's clock.

    An unknown or unavailable zone falls back to the runner's local time rather than
    failing: refusing to answer "may this guest reboot" over a typo in a timezone name
    would stop maintenance entirely, which is worse than running it on the clock the
    platform did use before this was configurable.
    """
    if not timezone:
        return datetime.datetime.now()
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)
    except Exception:
        return datetime.datetime.now()


def combine(resolved):
    """Intersect several resolved schedules -- the shared-host rule."""
    if len(resolved) == 1:
        return resolved[0]["mode"], resolved[0]["minutes"], None

    minutes = set(range(WEEK))
    for entry in resolved:
        minutes &= entry["minutes"]

    nevers = [entry["source"] for entry in resolved if entry["mode"] == "never"]
    if nevers:
        return "never", set(), (
            "an app sharing this guest is scheduled `never`, so the guest is never "
            "rebooted automatically: " + ", ".join(nevers)
        )
    if not minutes:
        return "never", set(), (
            "the apps sharing this guest declare windows that never overlap, so there "
            "is no time the guest may reboot without disrupting one of them outside "
            "its window: "
            + "; ".join("%s = %s" % (e["source"], e["text"]) for e in resolved)
        )
    if len(minutes) == WEEK:
        return "always", minutes, None
    return "window", minutes, None


def main():
    try:
        request = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write("maintenance-schedule: request is not JSON: %s\n" % exc)
        return 1

    try:
        chains = request.get("chains")
        if not isinstance(chains, list) or not chains:
            raise ScheduleError("request needs a non-empty `chains` list")

        resolved = []
        for index, chain in enumerate(chains):
            declaration, source = resolve_chain(chain, index)
            mode, minutes = minutes_of(declaration, "schedule from %s" % source)
            resolved.append({
                "source": source,
                "mode": mode,
                "minutes": minutes,
                "text": describe(mode, minutes),
            })

        mode, minutes, conflict = combine(resolved)

        if request.get("now"):
            try:
                now = datetime.datetime.fromisoformat(str(request["now"]))
            except ValueError as exc:
                raise ScheduleError(
                    "now %r is not an ISO timestamp: %s" % (request["now"], exc)
                )
        else:
            now = now_in(request.get("timezone") or "")
        now_minute = now.weekday() * DAY + now.hour * 60 + now.minute

        cron, cron_note = cron_of(mode, minutes)

        answer = {
            "mode": mode,
            "due": now_minute in minutes,
            "text": describe(mode, minutes),
            "cron": cron,
            "cron_note": cron_note,
            "next_open": next_open(mode, minutes, now),
            "monitor_only": mode == "never",
            "conflict": conflict,
            "sources": [
                {"source": e["source"], "mode": e["mode"], "text": e["text"]}
                for e in resolved
            ],
            "now": now.replace(second=0, microsecond=0).isoformat(),
            "timezone": request.get("timezone") or "",
        }
    except ScheduleError as exc:
        sys.stderr.write("maintenance-schedule: %s\n" % exc)
        return 1

    json.dump(answer, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
