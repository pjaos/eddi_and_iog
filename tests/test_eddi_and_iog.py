"""
tests/test_eddi_and_iog.py
==========================
Pytest test suite for the eddi_and_iog project.

Tests are fully self-contained and use unittest.mock to avoid any real
network calls to either the Octopus Energy API or the myenergi server.

Run with:
    pytest tests/test_eddi_and_iog.py -v
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# ---------------------------------------------------------------------------
# Adjust sys.path so tests can import the src layout without installing
# ---------------------------------------------------------------------------
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from eddi_and_iog.octopus import OctopusClient
from eddi_and_iog.myenergi import MyEnergi
from eddi_and_iog.eddi_and_iog import EddiSyncApp


# ===========================================================================
# Helpers
# ===========================================================================

def _utc(hour, minute, day=1, month=1, year=2024):
    """Return a UTC-aware datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _make_octopus(**kwargs):
    """Return an OctopusClient with dummy credentials."""
    defaults = dict(api_key="sk_live_test", account_number="A-TEST1234")
    defaults.update(kwargs)
    return OctopusClient(**defaults)


def _make_myenergi():
    me = MyEnergi("dummy_api_key")
    me.set_eddi_serial_number("12345678")
    return me


# ===========================================================================
# OctopusClient – _parse_dt
# ===========================================================================

class TestParseDt:
    def test_iso_with_utc_offset(self):
        dt = OctopusClient._parse_dt("2024-01-01T02:00:00+00:00")
        assert dt.tzinfo is not None
        assert dt.hour == 2

    def test_iso_naive_becomes_utc(self):
        dt = OctopusClient._parse_dt("2024-01-01T03:30:00")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 3

    def test_iso_with_positive_offset(self):
        dt = OctopusClient._parse_dt("2024-01-01T12:00:00+01:00")
        assert dt.tzinfo is not None
        # Converted to aware but NOT normalised – tzinfo preserved
        assert dt.hour == 12


# ===========================================================================
# OctopusClient – _is_outside_offpeak
# ===========================================================================

class TestIsOutsideOffpeak:
    """
    Off-peak window: 23:30 – 05:30 LOCAL time.
    Slots *inside* that window are handled by the standard overnight schedule
    and should NOT be treated as extra dispatches.

    The implementation calls .astimezone() on the inputs, so we must supply
    datetimes that represent the intended LOCAL wall-clock times — regardless
    of what UTC offset the test machine is on.  We do this by converting a
    naive datetime (which Python treats as local time) to an aware one via
    .astimezone(), which works correctly on any host timezone.
    """

    def setup_method(self):
        self.client = _make_octopus()

    @staticmethod
    def _local_dt(hour, minute, day=1):
        """
        Return an aware datetime whose LOCAL wall-clock time is hour:minute on
        2024-01-<day>.  Using naive→astimezone() conversion ensures the result
        reflects the host's actual local timezone, so the times the
        _is_outside_offpeak algorithm sees are exactly what we intend.
        """
        naive = datetime(2024, 1, day, hour, minute)
        return naive.astimezone()

    # --- slots fully inside the off-peak window ----------------------------

    def test_midnight_slot_is_inside(self):
        """00:00-01:00 local is well within 23:30-05:30 — not an extra slot."""
        s = self._local_dt(0, 0)
        e = self._local_dt(1, 0)
        assert self.client._is_outside_offpeak(s, e) is False

    def test_exact_offpeak_boundary_is_inside(self):
        """23:30-05:30 local exactly is the standard window — not extra."""
        s = self._local_dt(23, 30)
        e = self._local_dt(5, 30, day=2)
        assert self.client._is_outside_offpeak(s, e) is False

    def test_short_slot_inside_window(self):
        """01:00-02:00 local should not be treated as extra."""
        s = self._local_dt(1, 0)
        e = self._local_dt(2, 0)
        assert self.client._is_outside_offpeak(s, e) is False

    # --- slots fully outside or extending beyond the window ----------------

    def test_midday_slot_is_extra(self):
        """12:00-13:00 local is clearly outside — should be treated as extra."""
        s = self._local_dt(12, 0)
        e = self._local_dt(13, 0)
        assert self.client._is_outside_offpeak(s, e) is True

    def test_slot_ending_past_offpeak_end_is_extra(self):
        """02:00-06:00 local ends after 05:30 — extra."""
        s = self._local_dt(2, 0)
        e = self._local_dt(6, 0)
        assert self.client._is_outside_offpeak(s, e) is True

    def test_evening_slot_is_extra(self):
        """20:00-21:00 local is before 23:30 — extra."""
        s = self._local_dt(20, 0)
        e = self._local_dt(21, 0)
        assert self.client._is_outside_offpeak(s, e) is True


# ===========================================================================
# OctopusClient – token/device/dispatch plumbing (mocked HTTP)
# ===========================================================================

class TestOctopusClientHTTP:
    """Test the HTTP-facing methods with requests.post patched out."""

    TOKEN_RESPONSE = {
        "data": {"obtainKrakenToken": {"token": "test_token_abc"}}
    }
    DEVICE_RESPONSE = {
        "data": {"devices": [{"id": "dev_001", "deviceType": "ELECTRIC_VEHICLES"}]}
    }

    def _mock_post(self, *response_bodies):
        """Return a side_effect list of mock Response objects."""
        responses = []
        for body in response_bodies:
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = body
            r.raise_for_status.return_value = None
            responses.append(r)
        return responses

    @patch("eddi_and_iog.octopus.requests.post")
    def test_get_token_success(self, mock_post):
        mock_post.return_value = self._mock_post(self.TOKEN_RESPONSE)[0]
        client = _make_octopus()
        token = client._get_token()
        assert token == "test_token_abc"

    @patch("eddi_and_iog.octopus.requests.post")
    def test_get_token_cached(self, mock_post):
        """Second call should reuse the cached token without another HTTP call."""
        mock_post.return_value = self._mock_post(self.TOKEN_RESPONSE)[0]
        client = _make_octopus()
        client._get_token()
        client._get_token()
        assert mock_post.call_count == 1

    @patch("eddi_and_iog.octopus.requests.post")
    def test_get_token_returns_none_on_empty_response(self, mock_post):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"data": {"obtainKrakenToken": {}}}
        r.raise_for_status.return_value = None
        mock_post.return_value = r
        client = _make_octopus()
        assert client._get_token() is None

    @patch("eddi_and_iog.octopus.requests.post")
    def test_get_token_handles_exception(self, mock_post):
        mock_post.side_effect = Exception("Network error")
        client = _make_octopus()
        assert client._get_token() is None

    @patch("eddi_and_iog.octopus.requests.post")
    def test_get_device_id_found(self, mock_post):
        mock_post.side_effect = self._mock_post(
            self.TOKEN_RESPONSE,
            self.DEVICE_RESPONSE,
        )
        client = _make_octopus()
        device_id = client._get_device_id()
        assert device_id == "dev_001"

    @patch("eddi_and_iog.octopus.requests.post")
    def test_get_device_id_cached(self, mock_post):
        mock_post.side_effect = self._mock_post(
            self.TOKEN_RESPONSE,
            self.DEVICE_RESPONSE,
        )
        client = _make_octopus()
        client._get_device_id()
        client._get_device_id()
        assert mock_post.call_count == 2  # token + devices, then both cached

    @patch("eddi_and_iog.octopus.requests.post")
    def test_get_device_id_no_ev_device(self, mock_post):
        no_ev = {"data": {"devices": [{"id": "d2", "deviceType": "HEAT_PUMP"}]}}
        mock_post.side_effect = self._mock_post(self.TOKEN_RESPONSE, no_ev)
        client = _make_octopus()
        assert client._get_device_id() is None

    @patch("eddi_and_iog.octopus.requests.post")
    def test_find_active_extra_dispatch_returns_active(self, mock_post):
        """A dispatch active right now that extends outside 23:30-05:30."""
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=10)).isoformat()
        end   = (now + timedelta(minutes=50)).isoformat()
        dispatches_response = {
            "data": {"flexPlannedDispatches": [{"start": start, "end": end}]}
        }
        mock_post.side_effect = self._mock_post(
            self.TOKEN_RESPONSE,
            self.DEVICE_RESPONSE,
            dispatches_response,
        )
        client = _make_octopus()
        # Override _is_outside_offpeak so it always reports "extra"
        client._is_outside_offpeak = MagicMock(return_value=True)
        result = client.find_active_extra_dispatch()
        assert result is not None
        assert "start" in result and "end" in result

    @patch("eddi_and_iog.octopus.requests.post")
    def test_find_active_extra_dispatch_returns_none_when_inside_offpeak(self, mock_post):
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=10)).isoformat()
        end   = (now + timedelta(minutes=20)).isoformat()
        dispatches_response = {
            "data": {"flexPlannedDispatches": [{"start": start, "end": end}]}
        }
        mock_post.side_effect = self._mock_post(
            self.TOKEN_RESPONSE,
            self.DEVICE_RESPONSE,
            dispatches_response,
        )
        client = _make_octopus()
        client._is_outside_offpeak = MagicMock(return_value=False)
        assert client.find_active_extra_dispatch() is None

    @patch("eddi_and_iog.octopus.requests.post")
    def test_find_active_extra_dispatch_skips_future(self, mock_post):
        """A dispatch that hasn't started yet should not be returned."""
        now = datetime.now(timezone.utc)
        start = (now + timedelta(hours=2)).isoformat()
        end   = (now + timedelta(hours=3)).isoformat()
        dispatches_response = {
            "data": {"flexPlannedDispatches": [{"start": start, "end": end}]}
        }
        mock_post.side_effect = self._mock_post(
            self.TOKEN_RESPONSE,
            self.DEVICE_RESPONSE,
            dispatches_response,
        )
        client = _make_octopus()
        client._is_outside_offpeak = MagicMock(return_value=True)
        assert client.find_active_extra_dispatch() is None

    @patch("eddi_and_iog.octopus.requests.post")
    def test_find_active_extra_dispatch_skips_expired(self, mock_post):
        """A dispatch that finished in the past should not be returned."""
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=3)).isoformat()
        end   = (now - timedelta(hours=1)).isoformat()
        dispatches_response = {
            "data": {"flexPlannedDispatches": [{"start": start, "end": end}]}
        }
        mock_post.side_effect = self._mock_post(
            self.TOKEN_RESPONSE,
            self.DEVICE_RESPONSE,
            dispatches_response,
        )
        client = _make_octopus()
        client._is_outside_offpeak = MagicMock(return_value=True)
        assert client.find_active_extra_dispatch() is None

    @patch("eddi_and_iog.octopus.requests.post")
    def test_token_refresh_on_expiry_error(self, mock_post):
        """When Octopus returns a token-expired error the client should refresh."""
        expired_response = {
            "errors": [{"extensions": {"errorCode": "KT-CT-1124"}}]
        }
        dispatches_response = {"data": {"flexPlannedDispatches": []}}
        mock_post.side_effect = self._mock_post(
            self.TOKEN_RESPONSE,      # initial token
            self.DEVICE_RESPONSE,     # device lookup
            expired_response,         # first dispatch fetch → expired
            self.TOKEN_RESPONSE,      # token refresh
            dispatches_response,      # retry dispatch fetch
        )
        client = _make_octopus()
        result = client._get_planned_dispatches()
        assert result == []

    def test_is_token_expired_true(self):
        client = _make_octopus()
        data = {"errors": [{"extensions": {"errorCode": "KT-CT-1124"}}]}
        assert client._is_token_expired(data) is True

    def test_is_token_expired_false(self):
        client = _make_octopus()
        assert client._is_token_expired({"data": {}}) is False

    def test_is_token_expired_empty(self):
        client = _make_octopus()
        assert client._is_token_expired({}) is False


# ===========================================================================
# MyEnergi – static / pure helpers
# ===========================================================================

class TestMyEnergiGetTankId:
    def test_top(self):
        assert MyEnergi.get_tank_id("TOP") == MyEnergi.TANK_TOP

    def test_top_lowercase(self):
        assert MyEnergi.get_tank_id("top") == MyEnergi.TANK_TOP

    def test_bottom(self):
        assert MyEnergi.get_tank_id("BOTTOM") == MyEnergi.TANK_BOTTOM

    def test_invalid_raises(self):
        with pytest.raises(Exception):
            MyEnergi.get_tank_id("MIDDLE")


class TestMyEnergiDayOfWeekString:
    """_get_day_of_week_string should return the correct bitmask."""

    def setup_method(self):
        self.me = _make_myenergi()

    @pytest.mark.parametrize("day,expected", [
        (0, "01000000"),  # Monday
        (1, "00100000"),  # Tuesday
        (2, "00010000"),  # Wednesday
        (3, "00001000"),  # Thursday
        (4, "00000100"),  # Friday
        (5, "00000010"),  # Saturday
        (6, "00000001"),  # Sunday
    ])
    def test_all_days(self, day, expected):
        assert self.me._get_day_of_week_string(day) == expected

    def test_invalid_day_raises(self):
        with pytest.raises(Exception):
            self.me._get_day_of_week_string(7)


class TestMyEnergiSchedDayList:
    def setup_method(self):
        self.me = _make_myenergi()

    def test_all_days(self):
        result = self.me._get_sched_day_list("01111111")
        assert result == "Mon,Tue,Wed,Thu,Fri,Sat,Sun"

    def test_weekdays_only(self):
        result = self.me._get_sched_day_list("01111100")
        assert result == "Mon,Tue,Wed,Thu,Fri"

    def test_no_days(self):
        result = self.me._get_sched_day_list("00000000")
        assert result == ""

    def test_short_string_returns_empty(self):
        """Malformed bdd strings shorter than 8 chars yield an empty list."""
        result = self.me._get_sched_day_list("111")
        assert result == ""


class TestMyEnergiEddiScheduleString:
    """_get_eddi_schedule_string should format the API URL fragment correctly."""

    def setup_method(self):
        self.me = _make_myenergi()

    def test_on_top_tank(self):
        # Monday 14:30, 1 h 15 min
        dt = datetime(2024, 1, 1, 14, 30)   # Monday
        dur = timedelta(hours=1, minutes=15)
        s = self.me._get_eddi_schedule_string(True, dt, dur, MyEnergi.TOP_TANK_ID)
        # slot_id=14, time=1430, duration=115, day=Monday=01000000
        assert s == "14-1430-115-01000000"

    def test_on_bottom_tank(self):
        dt = datetime(2024, 1, 2, 2, 0)    # Tuesday
        dur = timedelta(hours=2, minutes=0)
        s = self.me._get_eddi_schedule_string(True, dt, dur, MyEnergi.BOTTOM_TANK_ID)
        assert s == "24-0200-200-00100000"

    def test_off_produces_zero_schedule(self):
        s = self.me._get_eddi_schedule_string(False, None, None, MyEnergi.TOP_TANK_ID)
        assert s == "14-0000-000-00000000"

    def test_off_bottom_tank(self):
        s = self.me._get_eddi_schedule_string(False, None, None, MyEnergi.BOTTOM_TANK_ID)
        assert s == "24-0000-000-00000000"

    def test_invalid_tank_raises(self):
        with pytest.raises(Exception):
            self.me._get_eddi_schedule_string(True, datetime.now(), timedelta(hours=1), 99)


class TestMyEnergiValidBoostDict:
    def setup_method(self):
        self.me = _make_myenergi()

    def _full_boost_dict(self):
        return {
            MyEnergi.BDD_BOOST_DICT_KEY: "01111111",
            MyEnergi.BDH_BOOST_DICT_KEY: 1,
            MyEnergi.BDM_BOOST_DICT_KEY: 0,
            MyEnergi.BSH_BOOST_DICT_KEY: 23,
            MyEnergi.BSM_BOOST_DICT_KEY: 30,
            MyEnergi.SLT_BOOST_DICT_KEY: 11,
        }

    def test_valid_boost_dict(self):
        assert self.me._is_valid_boost_dict(self._full_boost_dict()) is True

    def test_missing_one_key_is_invalid(self):
        d = self._full_boost_dict()
        del d[MyEnergi.BSH_BOOST_DICT_KEY]
        assert self.me._is_valid_boost_dict(d) is False

    def test_empty_dict_is_invalid(self):
        assert self.me._is_valid_boost_dict({}) is False


# ===========================================================================
# MyEnergi – set_tank_schedule (mocked HTTP)
# ===========================================================================

class TestMyEnergiSetTankSchedule:
    def setup_method(self):
        self.me = _make_myenergi()

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_set_tank_schedule_on_calls_correct_url(self, mock_get):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {}
        mock_get.return_value = r

        dt = datetime(2024, 1, 1, 2, 0)   # Monday
        dur = timedelta(hours=1)
        self.me.set_tank_schedule(True, dt, dur, MyEnergi.TOP_TANK_ID)

        called_url = mock_get.call_args[0][0]
        assert "cgi-boost-time-E" in called_url
        assert "12345678" in called_url

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_set_tank_schedule_off_sends_zero_string(self, mock_get):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {}
        mock_get.return_value = r

        self.me.set_tank_schedule(False, None, None, MyEnergi.TOP_TANK_ID)

        called_url = mock_get.call_args[0][0]
        assert "0000-000-00000000" in called_url

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_exec_api_cmd_raises_on_non_200(self, mock_get):
        r = MagicMock()
        r.status_code = 500
        mock_get.return_value = r
        with pytest.raises(Exception, match="500"):
            self.me._exec_api_cmd("http://example.com/test")

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_exec_api_cmd_raises_on_bad_status_field(self, mock_get):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {"status": -1}
        mock_get.return_value = r
        with pytest.raises(Exception):
            self.me._exec_api_cmd("http://example.com/test")


# ===========================================================================
# MyEnergi – serial number guards
# ===========================================================================

class TestMyEnergiSerialNumberGuards:
    def test_check_eddi_sn_raises_when_none(self):
        me = MyEnergi("key")
        me._eddi_serial_number = None
        with pytest.raises(Exception, match="eddi serial number"):
            me._check_eddi_serial_number()

    def test_check_zappi_sn_raises_when_none(self):
        me = MyEnergi("key")
        me._zappi_serial_number = None
        with pytest.raises(Exception, match="zappi serial number"):
            me._check_zappi_serial_number()


# ===========================================================================
# EddiSyncApp – fmt_time
# ===========================================================================

class TestEddiSyncAppFmtTime:
    @staticmethod
    def _local_aware(hour, minute):
        """Build an aware datetime at the given LOCAL wall-clock time."""
        return datetime(2024, 1, 1, hour, minute).astimezone()

    def test_fmt_time_formats_correctly(self):
        dt = self._local_aware(14, 35)
        assert EddiSyncApp.fmt_time(dt) == "14:35"

    def test_fmt_time_pads_single_digit_minute(self):
        dt = self._local_aware(9, 5)
        assert EddiSyncApp.fmt_time(dt) == "09:05"

    def test_fmt_time_midnight(self):
        dt = self._local_aware(0, 0)
        assert EddiSyncApp.fmt_time(dt) == "00:00"


# ===========================================================================
# EddiSyncApp – _poll / _handle_active_dispatch / _handle_no_dispatch
# ===========================================================================

class TestEddiSyncAppPoll:
    def _make_app(self):
        octopus = MagicMock(spec=OctopusClient)
        myenergi = MagicMock(spec=MyEnergi)
        myenergi.get_eddi_serial_number.return_value = "12345678"
        with patch.dict(os.environ, {"MYENERGI_EDDI_TANK": "TOP"}):
            app = EddiSyncApp(octopus, myenergi, poll_interval=180)
        return app, octopus, myenergi

    def _make_dispatch(self, start_offset_mins=-10, duration_mins=60):
        now = datetime.now(timezone.utc)
        start = now + timedelta(minutes=start_offset_mins)
        end   = start + timedelta(minutes=duration_mins)
        return {"start": start, "end": end, "raw": {}}

    # --- new dispatch arrives ------------------------------------------------

    def test_new_dispatch_sets_schedule_and_flag(self):
        app, octopus, myenergi = self._make_app()
        dispatch = self._make_dispatch()
        octopus.find_active_extra_dispatch.return_value = dispatch

        app._poll()

        myenergi.set_tank_schedule.assert_called_once_with(
            True,
            dispatch["start"],
            dispatch["end"] - dispatch["start"],
            app._tank,
        )
        assert app._slot_active is True
        assert app._active_end == dispatch["end"]

    # --- dispatch already active, end time unchanged -------------------------

    def test_existing_dispatch_no_change_skips_api_call(self):
        app, octopus, myenergi = self._make_app()
        dispatch = self._make_dispatch()
        app._slot_active = True
        app._active_end  = dispatch["end"]
        octopus.find_active_extra_dispatch.return_value = dispatch

        app._poll()

        myenergi.set_tank_schedule.assert_not_called()

    # --- dispatch end time shifts --------------------------------------------

    def test_dispatch_end_time_change_updates_schedule(self):
        app, octopus, myenergi = self._make_app()
        original_end = datetime.now(timezone.utc) + timedelta(minutes=30)
        dispatch = self._make_dispatch(duration_mins=90)   # new end further out
        app._slot_active = True
        app._active_end  = original_end
        octopus.find_active_extra_dispatch.return_value = dispatch

        app._poll()

        myenergi.set_tank_schedule.assert_called_once()
        assert app._active_end == dispatch["end"]

    # --- dispatch ends -------------------------------------------------------

    def test_dispatch_cleared_when_no_dispatch(self):
        app, octopus, myenergi = self._make_app()
        app._slot_active = True
        app._active_end  = datetime.now(timezone.utc)
        octopus.find_active_extra_dispatch.return_value = None

        app._poll()

        myenergi.set_tank_schedule.assert_called_once_with(
            False, None, None, app._tank,
        )
        assert app._slot_active is False
        assert app._active_end is None

    # --- no dispatch, was never active ---------------------------------------

    def test_no_dispatch_and_not_active_does_nothing(self):
        app, octopus, myenergi = self._make_app()
        octopus.find_active_extra_dispatch.return_value = None
        assert app._slot_active is False

        app._poll()

        myenergi.set_tank_schedule.assert_not_called()

    # --- poll interval floor -------------------------------------------------

    def test_poll_interval_floored_at_60(self):
        octopus  = MagicMock(spec=OctopusClient)
        myenergi = MagicMock(spec=MyEnergi)
        myenergi.get_eddi_serial_number.return_value = "12345678"
        with patch.dict(os.environ, {"MYENERGI_EDDI_TANK": "TOP"}):
            app = EddiSyncApp(octopus, myenergi, poll_interval=10)
        assert app.poll_interval == 60

    def test_poll_interval_above_60_unchanged(self):
        octopus  = MagicMock(spec=OctopusClient)
        myenergi = MagicMock(spec=MyEnergi)
        myenergi.get_eddi_serial_number.return_value = "12345678"
        with patch.dict(os.environ, {"MYENERGI_EDDI_TANK": "TOP"}):
            app = EddiSyncApp(octopus, myenergi, poll_interval=300)
        assert app.poll_interval == 300


# ===========================================================================
# EddiSyncApp – create_template_env_file
# ===========================================================================

class TestCreateTemplateEnvFile:
    def test_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        uio = MagicMock()
        EddiSyncApp.create_template_env_file(uio)
        env_file = tmp_path / "eddi_and_iog.env"
        assert env_file.exists()
        content = env_file.read_text()
        assert "OCTOPUS_API_KEY" in content
        assert "MYENERGI_API_KEY" in content

    def test_raises_if_file_already_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        env_file = tmp_path / "eddi_and_iog.env"
        env_file.write_text("existing")
        uio = MagicMock()
        with pytest.raises(Exception, match="already present"):
            EddiSyncApp.create_template_env_file(uio)


# ===========================================================================
# Integration-style: full poll cycle (two iterations)
# ===========================================================================

class TestEddiSyncAppFullCycle:
    """
    Simulate a complete activation + deactivation cycle without real I/O.
    """

    def test_activate_then_deactivate(self):
        octopus  = MagicMock(spec=OctopusClient)
        myenergi = MagicMock(spec=MyEnergi)
        myenergi.get_eddi_serial_number.return_value = "99887766"

        now      = datetime.now(timezone.utc)
        dispatch = {"start": now - timedelta(minutes=5),
                    "end":   now + timedelta(minutes=55),
                    "raw":   {}}

        with patch.dict(os.environ, {"MYENERGI_EDDI_TANK": "TOP"}):
            app = EddiSyncApp(octopus, myenergi, poll_interval=180)

        # --- iteration 1: dispatch is active ---
        octopus.find_active_extra_dispatch.return_value = dispatch
        app._poll()
        assert app._slot_active is True
        myenergi.set_tank_schedule.assert_called_once_with(
            True, dispatch["start"], dispatch["end"] - dispatch["start"], app._tank
        )

        # --- iteration 2: dispatch has ended ---
        octopus.find_active_extra_dispatch.return_value = None
        app._poll()
        assert app._slot_active is False
        # Second call should be the "clear" call
        assert myenergi.set_tank_schedule.call_count == 2
        second_call_args = myenergi.set_tank_schedule.call_args_list[1]
        assert second_call_args == ((False, None, None, app._tank),)
