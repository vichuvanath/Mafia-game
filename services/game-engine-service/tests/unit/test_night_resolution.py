"""Unit tests for night action resolution matrix."""

import pytest

from app.engine.night_resolution import get_public_night_announcement, resolve_night


@pytest.mark.parametrize(
    "mafia_target,doctor_target,expected_killed,expected_saved,expected_someone_died",
    [
        # 1. Same target -> Saved, nobody dies
        ("p1", "p1", None, True, False),
        # 2. Different target -> Mafia target dies
        ("p1", "p2", "p1", False, True),
        # 3. No Mafia action -> Nobody dies
        (None, "p2", None, False, False),
        # 4. No Doctor action -> Mafia target dies
        ("p1", None, "p1", False, True),
        # 5. Both missing -> Nobody dies
        (None, None, None, False, False),
    ],
)
def test_night_resolution_matrix(
    mafia_target: str | None,
    doctor_target: str | None,
    expected_killed: str | None,
    expected_saved: bool,
    expected_someone_died: bool,
) -> None:
    res = resolve_night(
        mafia_target_id=mafia_target,
        doctor_target_id=doctor_target,
    )
    assert res.killed_player_id == expected_killed
    assert res.is_saved == expected_saved
    assert res.someone_died == expected_someone_died


def test_doctor_self_protection_enabled() -> None:
    # Doctor targets self and Mafia targets Doctor
    res = resolve_night(
        mafia_target_id="doc_1",
        doctor_target_id="doc_1",
        doctor_id="doc_1",
        doctor_can_self_protect=True,
    )
    assert res.killed_player_id is None
    assert res.is_saved is True
    assert res.someone_died is False


def test_doctor_self_protection_disabled() -> None:
    # Doctor targets self, Mafia targets Doctor, but self-protection is disabled
    res = resolve_night(
        mafia_target_id="doc_1",
        doctor_target_id="doc_1",
        doctor_id="doc_1",
        doctor_can_self_protect=False,
    )
    # Doctor's self-protection is void; Doctor is killed
    assert res.killed_player_id == "doc_1"
    assert res.is_saved is False
    assert res.someone_died is True


def test_doctor_self_protection_disabled_protecting_other() -> None:
    # Doctor targets someone else when self-protection is disabled
    res = resolve_night(
        mafia_target_id="p2",
        doctor_target_id="p2",
        doctor_id="doc_1",
        doctor_can_self_protect=False,
    )
    # Protecting another player is fully effective
    assert res.killed_player_id is None
    assert res.is_saved is True
    assert res.someone_died is False


def test_public_night_announcement_secrecy() -> None:
    # When someone died
    kill_res = resolve_night(mafia_target_id="victim_1", doctor_target_id="other_1")
    announcement = get_public_night_announcement(kill_res)
    assert announcement["someone_died"] is True
    assert announcement["killed_player_id"] == "victim_1"
    # Ensure role names and protection details are strictly absent
    msg = announcement["message"].lower()
    assert "mafia" not in msg
    assert "doctor" not in msg
    assert "protected" not in msg

    # When saved (nobody died)
    save_res = resolve_night(mafia_target_id="victim_1", doctor_target_id="victim_1")
    announcement_saved = get_public_night_announcement(save_res)
    assert announcement_saved["someone_died"] is False
    assert announcement_saved["killed_player_id"] is None
    msg_saved = announcement_saved["message"].lower()
    # It must only say nobody died, not who was saved or by whom
    assert "mafia" not in msg_saved
    assert "doctor" not in msg_saved
    assert "saved" not in msg_saved
    assert "victim_1" not in msg_saved
