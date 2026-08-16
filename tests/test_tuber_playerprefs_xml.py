"""Minimal PlayerPrefs XML parse/write tests mirroring the Android editor."""

from __future__ import annotations

import re

ENTRY_RE = re.compile(
    r'<(int|float|long|boolean|string)\s+name="([^"]+)"(?:\s+value="([^"]*)")?\s*(?:/>|>(.*?)</string>)',
    re.I | re.S,
)


def parse(xml: str) -> list[tuple[str, str, str]]:
    out = []
    for m in ENTRY_RE.finditer(xml):
        typ, name, attr, body = m.group(1).lower(), m.group(2), m.group(3), m.group(4)
        val = body if typ == "string" else attr
        out.append((name, typ, val or ""))
    return out


def test_parse_playerprefs_sample():
    xml = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <int name="Bux" value="12345" />
    <int name="TotalSubscribers" value="999" />
    <float name="Knowledge" value="12.5" />
    <string name="ChannelName">MyTuber</string>
    <boolean name="TutorialDone" value="true" />
</map>
"""
    entries = parse(xml)
    by_name = {n: (t, v) for n, t, v in entries}
    assert by_name["Bux"] == ("int", "12345")
    assert by_name["TotalSubscribers"] == ("int", "999")
    assert by_name["Knowledge"] == ("float", "12.5")
    assert by_name["ChannelName"] == ("string", "MyTuber")
    assert by_name["TutorialDone"] == ("boolean", "true")


def test_needle_match_for_quick_fields():
    entries = parse(
        '<map><int name="PlayerSoftBuxAmount" value="10" />'
        '<int name="LifetimeSubscriberCount" value="3" /></map>'
    )
    names = [n.lower() for n, _, _ in entries]
    assert any("bux" in n for n in names)
    assert any("subscriber" in n for n in names)
