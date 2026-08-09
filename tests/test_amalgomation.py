"""Tests for Amalgomation (secret fight id 666)."""

from __future__ import annotations

from undertale_extractor import amalgomation as am


def test_amalgomation_id():
    assert am.is_amalgomation_id(666) is True
    assert am.is_amalgomation_id(47) is False
    assert am.AMALGOMATION_ID == 666
    assert am.HOST_BATTLEGROUP == 86


def test_chaos_director_stacks_attacks():
    d = am.AmalgomationDirector()
    d.start()
    assert d.state.running is True
    assert len(d.state.stack) == 1
    # Simulate enough ticks to stack layers (every 8 ticks adds)
    for _ in range(24):
        d.tick()
    assert d.state.layer >= 2
    assert len(d.state.stack) >= 2
    assert d.state.fake_hp >= 1
    d.stop()
    assert d.state.running is False


def test_attack_pool_nonempty():
    assert len(am.ATTACK_POOL) >= 10
    assert len(am.DIALOGS) >= 5
