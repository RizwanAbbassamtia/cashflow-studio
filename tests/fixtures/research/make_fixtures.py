"""One-off generator for tests/fixtures/research/channels/*.json (recorded mock data)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])

TITLES_LONG = [
    "The waitress who kept a stranger's secret for 30 years",
    "He gave away his last dollar and got a letter 10 years later",
    "The night a whole town stayed up for one boy",
    "A janitor's notebook changed a hospital forever",
    "Why the old bus driver never missed a single stop",
    "The stranger who paid for every meal in the diner",
    "She found a wallet and discovered who her father really was",
    "The teacher who wrote 400 letters nobody asked for",
    "A mechanic fixed cars for free until the town noticed",
    "The last voicemail from a grandmother in Ohio",
    "A lost dog, a snowstorm, and the neighbour who would not quit",
    "The baker who left the lights on every night for one reason",
    "How a crossing guard became the heart of a small city",
    "The letter in the library book that waited 50 years",
    "A farmer's quiet promise to a boy he barely knew",
    "The woman who adopted a whole street of strangers",
    "Why the shop owner refused to close on Christmas Eve",
    "The pilot who turned the plane around for a stranger",
    "A homeless man returned the ring and changed his life",
    "The barber who cut hair in the park for 12 years",
    "An eight hour live conversation about kindness and loss",
    "The nurse who sang to patients nobody visited",
    "A boy mailed his allowance to a soldier every month",
    "The coach who never cut a single player",
    "The marathon runner who stopped to carry a stranger",
    "Recorded live: stories from our community night",
    "The widow who fed the whole crew for a year",
    "A train conductor's goodbye after 40 years on the line",
    "The mailman who learned sign language for one family",
    "The quiet neighbour nobody knew was a hero",
]
TITLES_SHORTS = [
    "He paid for her groceries and ran",
    "The note on the windshield",
    "A stranger's umbrella in the rain",
    "The boy who returned the bike",
    "Grandma's last text message",
    "The cashier kept the change for him",
    "Why she waits at the bus stop every day",
    "A dog brought the lost toddler home",
    "The teacher's secret lunch fund",
    "He fixed the stranger's flat tyre at midnight",
]

CHANNELS = [
    {
        "key": "human-ember",
        "name": "Human Ember",
        "url": "https://www.youtube.com/@humanember",
        "aliases": ["https://www.youtube.com/channel/UCFjva5hxOFoj2ViNgSuEQJg"],
        "channel_id": "UCFjva5hxOFoj2ViNgSuEQJg",
        "followers": 250000,
        "language": "English",
        "letter": "A",
        "base": 40000,
        "spread": 12000,
        "outlier_long": (5, 16.0),
        "outlier_short": (3, 15.0),
        "short_base": 15000,
    },
    {
        "key": "gentle-hour",
        "name": "The Gentle Hour",
        "url": "https://www.youtube.com/@thegentlehour",
        "aliases": [],
        "channel_id": "UCgentlehour000000000001",
        "followers": 90000,
        "language": "English",
        "letter": "B",
        "base": 12000,
        "spread": 4000,
        "outlier_long": (12, 16.0),
        "outlier_short": (6, 15.0),
        "short_base": 6000,
    },
    {
        "key": "calm-compass",
        "name": "Calm Compass",
        "url": "https://www.youtube.com/@calmcompass",
        "aliases": [],
        "channel_id": "UCcalmcompass00000000001",
        "followers": 400000,
        "language": "Spanish",
        "letter": "C",
        "base": 80000,
        "spread": 20000,
        "outlier_long": (4, 16.0),
        "outlier_short": (8, 15.0),
        "short_base": 30000,
    },
]


def views(base: int, spread: int, i: int) -> int:
    # Deterministic wobble around the base so neighbours differ but medians stay close.
    wobble = ((i * 7919) % (2 * spread + 1)) - spread
    return base + wobble


def main() -> None:
    for ch in CHANNELS:
        videos = []
        out_pos, out_mult = ch["outlier_long"]
        for i, title in enumerate(TITLES_LONG):
            v = views(ch["base"], ch["spread"], i)
            days_ago = 4 + i * 6.0
            duration = 480 + ((i * 97) % 420)  # 8-15 minutes
            live = None
            if i == out_pos:
                v = int(ch["base"] * out_mult)
            if i == 0:
                days_ago = 1.0  # too new: excluded, and left out of baselines
                v = int(ch["base"] * 2.2)
            if i == 20:
                duration = 8 * 3600  # an eight-hour video: excluded (longer than 40 minutes)
            if i == 25:
                live = "was_live"  # recorded live stream: excluded
            videos.append(
                {
                    "video_id": f"mk{ch['letter']}v{i:07d}",
                    "title": title,
                    "tab": "videos",
                    "position": i,
                    "duration_s": duration,
                    "view_count": v,
                    "days_ago": days_ago,
                    "live_status": live,
                }
            )
        s_pos, s_mult = ch["outlier_short"]
        for i, title in enumerate(TITLES_SHORTS):
            v = views(ch["short_base"], ch["short_base"] // 4, i)
            if i == s_pos:
                v = int(ch["short_base"] * s_mult)
            videos.append(
                {
                    "video_id": f"mk{ch['letter']}s{i:07d}",
                    "title": title,
                    "tab": "shorts",
                    "position": i,
                    "duration_s": 35 + (i * 11) % 40,
                    "view_count": v,
                    "days_ago": 5 + i * 4.0,
                    "live_status": None,
                }
            )
        fixture = {
            "key": ch["key"],
            "name": ch["name"],
            "url": ch["url"],
            "aliases": ch["aliases"],
            "channel_id": ch["channel_id"],
            "followers": ch["followers"],
            "language": ch["language"],
            "videos": videos,
        }
        path = OUT / f"{ch['key']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        print("wrote", path, len(videos), "videos")


if __name__ == "__main__":
    main()
