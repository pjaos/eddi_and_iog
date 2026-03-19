"""
Pytest test suite for EddiSyncApp in eddi_and_iog.py.

Both OctopusClient and MyEnergi are fully mocked — no network calls are made
and no real hardware is required.

Project layout assumed:
    <repo-root>/
        src/
            eddi_and_iog/
                eddi_and_iog.py   ← module under test
                myenergi.py
                octopus.py
        tests/
            test_eddi_and_iog.py  ← this file
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eddi_and_iog.eddi_and_iog import EddiSyncApp
from eddi_and_iog.myenergi import MyEnergi


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

def utc(hour, minute=0):
    """Convenience: return a UTC-aware datetime on a fixed date."""
    return datetime(2024, 6, 1, hour, minute, tzinfo=timezone.utc)


def make_dispatch(start_hour, start_min, end_hour, end_min):
    """Return a dispatch dict with UTC-aware datetimes."""
    return {
        "start": utc(start_hour, start_min),
        "end":   utc(end_hour,   end_min),
    }


@pytest.fixture
def mock_octopus():
    m = MagicMock()
    m.account_number = "A-TEST1234"
    return m


@pytest.fixture
def mock_myenergi():
    m = MagicMock(spec=MyEnergi)
    m.get_eddi_serial_number.return_value = "12345678"
    return m


@pytest.fixture
def app(mock_octopus, mock_myenergi):
    """EddiSyncApp with MYENERGI_EDDI_TANK=TOP and both dependencies mocked."""
    with patch.dict(os.environ, {"MYENERGI_EDDI_TANK": "TOP"}):
        instance = EddiSyncApp(
            octopus=mock_octopus,
            myenergy=mock_myenergi,
            poll_interval=180,
        )
    return instance


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestInit:
    def test_slot_inactive_on_start(self, app):
        assert app._slot_active is False

    def test_active_end_none_on_start(self, app):
        assert app._active_end is None

    def test_tank_resolved_to_top(self, app):
        assert app._tank == MyEnergi.TANK_TOP

    def test_tank_bottom_resolved(self, mock_octopus, mock_myenergi):
        with patch.dict(os.environ, {"MYENERGI_EDDI_TANK": "BOTTOM"}):
            instance = EddiSyncApp(mock_octopus, mock_myenergi)
        assert instance._tank == MyEnergi.BOTTOM_TANK_ID

    def test_poll_interval_stored(self, app):
        assert app.poll_interval == 180

    def test_invalid_tank_raises(self, mock_octopus, mock_myenergi):
        with patch.dict(os.environ, {"MYENERGI_EDDI_TANK": "MIDDLE"}):
            with pytest.raises(Exception):
                EddiSyncApp(mock_octopus, mock_myenergi)


# ---------------------------------------------------------------------------
# fmt_time (static helper)
# ---------------------------------------------------------------------------

class TestFmtTime:
    def test_returns_hhmm_string(self):
        # Use a fixed-offset timezone so the result is deterministic
        dt = datetime(2024, 6, 1, 14, 35, tzinfo=timezone.utc)
        result = EddiSyncApp.fmt_time(dt)
        # Result must be HH:MM format
        assert len(result) == 5
        assert result[2] == ":"

    def test_zero_padded(self):
        dt = datetime(2024, 6, 1, 8, 5, tzinfo=timezone.utc)
        result = EddiSyncApp.fmt_time(dt)
        assert ":" in result


# ---------------------------------------------------------------------------
# _poll — routing to the correct handler
# ---------------------------------------------------------------------------

class TestPoll:
    def test_routes_to_handle_active_when_dispatch_returned(self, app, mock_octopus):
        dispatch = make_dispatch(14, 0, 15, 30)
        mock_octopus.find_active_extra_dispatch.return_value = dispatch
        with patch.object(app, "_handle_active_dispatch") as mock_active, \
             patch.object(app, "_handle_no_dispatch") as mock_none:
            app._poll()
        mock_active.assert_called_once_with(dispatch)
        mock_none.assert_not_called()

    def test_routes_to_handle_no_dispatch_when_none_returned(self, app, mock_octopus):
        mock_octopus.find_active_extra_dispatch.return_value = None
        with patch.object(app, "_handle_active_dispatch") as mock_active, \
             patch.object(app, "_handle_no_dispatch") as mock_none:
            app._poll()
        mock_none.assert_called_once()
        mock_active.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_active_dispatch
# ---------------------------------------------------------------------------

class TestHandleActiveDispatch:
    def test_new_dispatch_sets_schedule(self, app, mock_myenergi):
        dispatch = make_dispatch(14, 0, 15, 30)
        app._handle_active_dispatch(dispatch)
        mock_myenergi.set_tank_schedule.assert_called_once_with(
            True,
            dispatch["start"],
            dispatch["end"] - dispatch["start"],
            app._tank,
        )

    def test_new_dispatch_marks_slot_active(self, app):
        dispatch = make_dispatch(14, 0, 15, 30)
        app._handle_active_dispatch(dispatch)
        assert app._slot_active is True

    def test_new_dispatch_stores_active_end(self, app):
        dispatch = make_dispatch(14, 0, 15, 30)
        app._handle_active_dispatch(dispatch)
        assert app._active_end == dispatch["end"]

    def test_same_dispatch_repeated_does_not_call_schedule_again(self, app, mock_myenergi):
        dispatch = make_dispatch(14, 0, 15, 30)
        # First call activates the slot
        app._handle_active_dispatch(dispatch)
        mock_myenergi.reset_mock()
        # Second call with identical dispatch — should NOT set schedule again
        app._handle_active_dispatch(dispatch)
        mock_myenergi.set_tank_schedule.assert_not_called()

    def test_changed_end_time_updates_schedule(self, app, mock_myenergi):
        dispatch_v1 = make_dispatch(14, 0, 15, 30)
        app._handle_active_dispatch(dispatch_v1)
        mock_myenergi.reset_mock()

        dispatch_v2 = make_dispatch(14, 0, 16, 0)  # end moved forward
        app._handle_active_dispatch(dispatch_v2)

        mock_myenergi.set_tank_schedule.assert_called_once_with(
            True,
            dispatch_v2["start"],
            dispatch_v2["end"] - dispatch_v2["start"],
            app._tank,
        )

    def test_changed_end_time_updates_active_end(self, app):
        dispatch_v1 = make_dispatch(14, 0, 15, 30)
        app._handle_active_dispatch(dispatch_v1)

        dispatch_v2 = make_dispatch(14, 0, 16, 0)
        app._handle_active_dispatch(dispatch_v2)

        assert app._active_end == dispatch_v2["end"]

    def test_duration_passed_correctly(self, app, mock_myenergi):
        start = utc(10, 0)
        end   = utc(11, 45)
        dispatch = {"start": start, "end": end}
        app._handle_active_dispatch(dispatch)
        _, call_start, call_duration, _ = mock_myenergi.set_tank_schedule.call_args[0]
        assert call_duration == timedelta(hours=1, minutes=45)


# ---------------------------------------------------------------------------
# _handle_no_dispatch
# ---------------------------------------------------------------------------

class TestHandleNoDispatch:
    def test_clears_schedule_when_slot_was_active(self, app, mock_myenergi):
        # Simulate a previously active slot
        app._slot_active = True
        app._active_end  = utc(15, 30)

        app._handle_no_dispatch()

        mock_myenergi.set_tank_schedule.assert_called_once_with(
            False, None, None, app._tank
        )

    def test_marks_slot_inactive_after_clearing(self, app):
        app._slot_active = True
        app._active_end  = utc(15, 30)
        app._handle_no_dispatch()
        assert app._slot_active is False

    def test_clears_active_end_after_clearing(self, app):
        app._slot_active = True
        app._active_end  = utc(15, 30)
        app._handle_no_dispatch()
        assert app._active_end is None

    def test_no_call_when_slot_was_already_inactive(self, app, mock_myenergi):
        app._slot_active = False
        app._handle_no_dispatch()
        mock_myenergi.set_tank_schedule.assert_not_called()


# ---------------------------------------------------------------------------
# Full _poll cycle sequences
# ---------------------------------------------------------------------------

class TestPollCycles:
    def test_activate_then_deactivate(self, app, mock_octopus, mock_myenergi):
        dispatch = make_dispatch(14, 0, 15, 30)

        # Poll 1: dispatch is live
        mock_octopus.find_active_extra_dispatch.return_value = dispatch
        app._poll()
        assert app._slot_active is True
        assert mock_myenergi.set_tank_schedule.call_count == 1

        # Poll 2: dispatch gone
        mock_octopus.find_active_extra_dispatch.return_value = None
        app._poll()
        assert app._slot_active is False
        # Second call should be set_tank_schedule(False, ...)
        assert mock_myenergi.set_tank_schedule.call_count == 2
        last_call = mock_myenergi.set_tank_schedule.call_args
        assert last_call == call(False, None, None, app._tank)

    def test_repeated_polls_with_same_dispatch_calls_schedule_once(self, app, mock_octopus, mock_myenergi):
        dispatch = make_dispatch(14, 0, 15, 30)
        mock_octopus.find_active_extra_dispatch.return_value = dispatch

        for _ in range(5):
            app._poll()

        # set_tank_schedule should only have been called on the first activation
        assert mock_myenergi.set_tank_schedule.call_count == 1

    def test_end_time_extension_mid_dispatch(self, app, mock_octopus, mock_myenergi):
        dispatch_v1 = make_dispatch(14, 0, 15, 30)
        dispatch_v2 = make_dispatch(14, 0, 16, 0)

        mock_octopus.find_active_extra_dispatch.return_value = dispatch_v1
        app._poll()  # activates slot

        mock_octopus.find_active_extra_dispatch.return_value = dispatch_v2
        app._poll()  # end time changed — should update

        assert mock_myenergi.set_tank_schedule.call_count == 2
        assert app._active_end == dispatch_v2["end"]

    def test_no_dispatch_never_active_never_calls_myenergi(self, app, mock_octopus, mock_myenergi):
        mock_octopus.find_active_extra_dispatch.return_value = None
        for _ in range(3):
            app._poll()
        mock_myenergi.set_tank_schedule.assert_not_called()

    def test_activate_deactivate_reactivate(self, app, mock_octopus, mock_myenergi):
        dispatch = make_dispatch(14, 0, 15, 30)

        mock_octopus.find_active_extra_dispatch.return_value = dispatch
        app._poll()   # activate

        mock_octopus.find_active_extra_dispatch.return_value = None
        app._poll()   # deactivate

        mock_octopus.find_active_extra_dispatch.return_value = dispatch
        app._poll()   # re-activate

        # set_tank_schedule called 3 times total: on, off, on
        assert mock_myenergi.set_tank_schedule.call_count == 3
        first, second, third = mock_myenergi.set_tank_schedule.call_args_list
        assert first[0][0]  is True
        assert second[0][0] is False
        assert third[0][0]  is True


# ---------------------------------------------------------------------------
# create_template_env_file
# ---------------------------------------------------------------------------

class TestCreateTemplateEnvFile:
    def test_creates_file_with_expected_keys(self, tmp_path):
        env_file = tmp_path / "eddi_and_iog.env"
        mock_uio = MagicMock()

        with patch("pathlib.Path.home", return_value=tmp_path):
            EddiSyncApp.create_template_env_file(mock_uio)

        assert env_file.exists()
        content = env_file.read_text()
        for key in ("OCTOPUS_API_KEY", "OCTOPUS_ACCOUNT_NO",
                    "MYENERGI_API_KEY", "MYENERGI_EDDI_SN", "MYENERGI_EDDI_TANK"):
            assert key in content

    def test_raises_if_file_already_exists(self, tmp_path):
        env_file = tmp_path / "eddi_and_iog.env"
        env_file.write_text("existing content")
        mock_uio = MagicMock()

        with patch("pathlib.Path.home", return_value=tmp_path):
            with pytest.raises(Exception, match="already present"):
                EddiSyncApp.create_template_env_file(mock_uio)

    def test_informs_user_after_creation(self, tmp_path):
        mock_uio = MagicMock()
        with patch("pathlib.Path.home", return_value=tmp_path):
            EddiSyncApp.create_template_env_file(mock_uio)
        mock_uio.info.assert_called()

    def test_tank_default_is_top(self, tmp_path):
        mock_uio = MagicMock()
        with patch("pathlib.Path.home", return_value=tmp_path):
            EddiSyncApp.create_template_env_file(mock_uio)
        content = (tmp_path / "eddi_and_iog.env").read_text()
        assert "MYENERGI_EDDI_TANK=TOP" in content
