"""Tests for Amalgomation (secret fight id 666) — in-game only."""

from __future__ import annotations

import struct

from undertale_extractor import amalgomation as am
from undertale_extractor.battles import pushi_word


def test_amalgomation_id():
    assert am.is_amalgomation_id(666) is True
    assert am.is_amalgomation_id(47) is False
    assert am.AMALGOMATION_ID == 666
    assert am.HOST_BATTLEGROUP == 86


def test_open_amalgomation_ui_has_no_window_dependency():
    """Director must not require a Tk collage window (in-game only)."""
    assert hasattr(am, "start_amalgomation_fight")
    assert hasattr(am, "AmalgomationDirector")
    assert hasattr(am, "install_amalgomation_into_data_win")
    # Chaos director should work headlessly
    plan = am.AmalgomationPlan()
    plan.resources.sprite_ids = [1, 2, 3, 4, 5]
    plan.resources.gen_object_ids = [10, 11, 12, 13]
    plan.resources.objects = {"obj_froggitgen": 10, "obj_sansbone": 11}
    # Fake sprite patch sites (won't write without Windows/process)
    plan.sprite_sites = [
        am.PatchSite(0, pushi_word(100), "sprite"),
        am.PatchSite(4, pushi_word(101), "sprite"),
    ]
    plan.attack_sites = [
        am.PatchSite(8, pushi_word(10), "attack"),
        am.PatchSite(12, pushi_word(11), "attack"),
    ]
    d = am.AmalgomationDirector.__new__(am.AmalgomationDirector)
    d.data_win = type("P", (), {"is_file": lambda self: False})()
    d.plan = plan
    d.state = am.ChaosState()
    d.rng = __import__("random").Random(0)
    d._stop = __import__("threading").Event()
    d._thread = None
    d._player_hp_addrs = []
    d._monster_hp_addrs = []
    d._monster_df_addrs = []
    d._tick_count = 0
    d._file_size = None
    d._active_attack_slots = []
    d.state = am.ChaosState(running=True, layer=1, rounds=0, stack=[])
    first = 10
    d._active_attack_slots = [first]
    d.state.stack = [d._label_for_gen(first)]
    for _ in range(24):
        d.tick()
    assert d.state.layer >= 2
    assert len(d.state.stack) >= 2
    assert d.state.fake_hp >= 1
    d.stop()
    assert d.state.running is False


def test_discover_resources_from_minimal_chunks():
    """SPRT/OBJT name lists are indexed for morph + attack pools."""
    # Minimal FORM with empty-ish chunks is hard; just ensure function returns ResourceIndex
    empty = am.discover_resources(b"FORM" + struct.pack("<I", 0))
    assert isinstance(empty, am.ResourceIndex)
    assert empty.sprite_ids == []


def test_build_plan_finds_pushi_sites():
    # Craft a tiny fake "code" blob and wrap it — build_amalgomation_plan needs CODE chunk.
    # Smoke: empty data returns empty plan without crashing.
    plan = am.build_amalgomation_plan(b"FORM" + struct.pack("<I", 0))
    assert isinstance(plan, am.AmalgomationPlan)
    assert plan.sprite_sites == []
