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
    assert am.OVERWORLD_SKIP_ROOM == 6


def test_autofight_helpers_exist():
    assert hasattr(am, "run_amalgomation_autofight")
    assert hasattr(am, "open_amalgomation_ui")
    assert hasattr(am, "prepare_amalgomation_plan")


def test_open_amalgomation_ui_has_no_window_dependency():
    """Director must not require a Tk collage window (in-game only)."""
    assert hasattr(am, "start_amalgomation_fight")
    assert hasattr(am, "AmalgomationDirector")
    assert hasattr(am, "install_amalgomation_into_data_win")
    plan = am.AmalgomationPlan()
    plan.resources.sprite_ids = [1, 2, 3, 4, 5]
    plan.resources.gen_object_ids = [10, 11, 12, 13]
    plan.resources.objects = {"obj_froggitgen": 10, "obj_sansbone": 11}
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


def test_restore_backup_helper(tmp_path):
    data = tmp_path / "data.win"
    bak = tmp_path / "data.win.amalgobak"
    data.write_bytes(b"DIRTY")
    bak.write_bytes(b"CLEAN")
    restored, msg = am.restore_amalgomation_backup_if_any(data)
    assert restored is True
    assert data.read_bytes() == b"CLEAN"
    assert "Restored" in msg
    restored2, msg2 = am.restore_amalgomation_backup_if_any(data)
    assert restored2 is False
    assert msg2 == ""


def test_prepare_continues_after_restore_when_requested(tmp_path):
    data = tmp_path / "data.win"
    bak = tmp_path / "data.win.amalgobak"
    # Minimal FORM so prepare does not crash
    data.write_bytes(b"DIRTYXXXX")
    bak.write_bytes(b"FORM" + struct.pack("<I", 0))
    ok, msg, plan = am.prepare_amalgomation_plan(data, abort_after_restore=False)
    assert data.read_bytes().startswith(b"FORM")
    assert "Restored" in msg or "Amalgomation ready" in msg
    assert isinstance(plan, am.AmalgomationPlan)


def test_prepare_plan_smoke():
    assert callable(am.prepare_amalgomation_plan)
