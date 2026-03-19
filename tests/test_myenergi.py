"""
Pytest test suite for the MyEnergi class.

All HTTP calls are mocked via unittest.mock so no real network access is required.

Project layout assumed:
    <repo-root>/
        src/
            eddi_and_iog/
                myenergi.py
        tests/
            test_myenergi.py        ← this file
        (optional) conftest.py / pyproject.toml / setup.cfg
"""

import sys
import os
from pathlib import Path

# Point at src/ so that eddi_and_iog is importable as a package.
# This matches how eddi_and_iog.py itself imports: from eddi_and_iog.myenergi import MyEnergi
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from eddi_and_iog.myenergi import MyEnergi


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

API_KEY = "test-api-key"
EDDI_SN = "12345678"
ZAPPI_SN = "87654321"


@pytest.fixture
def me():
    """Return a MyEnergi instance with serial numbers pre-set."""
    instance = MyEnergi(api_key=API_KEY)
    instance.set_eddi_serial_number(EDDI_SN)
    instance.set_zappi_serial_number(ZAPPI_SN)
    return instance


@pytest.fixture
def me_no_serials():
    """Return a MyEnergi instance with NO serial numbers set."""
    return MyEnergi(api_key=API_KEY)


def make_mock_response(json_data, status_code=200):
    """Helper: build a mock requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    return mock_resp


# ---------------------------------------------------------------------------
# Class constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_tank_top(self):
        assert MyEnergi.TANK_TOP == 1

    def test_tank_bottom(self):
        assert MyEnergi.TANK_BOTTOM == 2

    def test_zappi_charge_modes(self):
        assert MyEnergi.ZAPPI_CHARG_MODE_FAST == 1
        assert MyEnergi.ZAPPI_CHARGE_MODE_ECO == 2
        assert MyEnergi.ZAPPI_CHARGE_MODE_ECO_PLUS == 3
        assert MyEnergi.ZAPPI_CHARGE_MODE_STOPPED == 4

    def test_valid_eddi_slot_ids(self):
        expected = (11, 12, 13, 14, 21, 22, 23, 24)
        assert MyEnergi.VALID_EDDI_SLOT_ID_LIST == expected

    def test_valid_zappi_slot_ids(self):
        assert MyEnergi.VALID_ZAPPI_SLOT_ID_LIST == (11, 12, 13, 14)

    def test_boost_dict_keys_complete(self):
        assert len(MyEnergi.BOOST_DICT_KEYS) == 6


# ---------------------------------------------------------------------------
# Constructor & serial number helpers
# ---------------------------------------------------------------------------

class TestInit:
    def test_default_state(self):
        m = MyEnergi(api_key=API_KEY)
        assert m._api_key == API_KEY
        assert m._eddi_serial_number == ""
        assert m._zappi_serial_number == ""
        assert m._eddi_stats_dict is None
        assert m._zappi_stats_dict is None

    def test_set_and_get_eddi_serial_number(self):
        m = MyEnergi(api_key=API_KEY)
        m.set_eddi_serial_number(EDDI_SN)
        assert m.get_eddi_serial_number() == EDDI_SN

    def test_set_zappi_serial_number(self):
        m = MyEnergi(api_key=API_KEY)
        m.set_zappi_serial_number(ZAPPI_SN)
        assert m._zappi_serial_number == ZAPPI_SN

    def test_check_eddi_serial_number_raises_when_none(self):
        m = MyEnergi(api_key=API_KEY)
        m._eddi_serial_number = None  # explicitly set to None
        with pytest.raises(Exception, match="eddi serial number has not been set"):
            m._check_eddi_serial_number()

    def test_check_zappi_serial_number_raises_when_none(self):
        m = MyEnergi(api_key=API_KEY)
        m._zappi_serial_number = None
        with pytest.raises(Exception, match="zappi serial number has not been set"):
            m._check_zappi_serial_number()


# ---------------------------------------------------------------------------
# get_tank_id (static method)
# ---------------------------------------------------------------------------

class TestGetTankId:
    def test_top_lowercase(self):
        assert MyEnergi.get_tank_id("top") == MyEnergi.TANK_TOP

    def test_top_uppercase(self):
        assert MyEnergi.get_tank_id("TOP") == MyEnergi.TANK_TOP

    def test_top_mixed_case(self):
        assert MyEnergi.get_tank_id("Top") == MyEnergi.TANK_TOP

    def test_bottom_lowercase(self):
        assert MyEnergi.get_tank_id("bottom") == MyEnergi.TANK_BOTTOM

    def test_bottom_uppercase(self):
        assert MyEnergi.get_tank_id("BOTTOM") == MyEnergi.TANK_BOTTOM

    def test_invalid_raises(self):
        with pytest.raises(Exception):
            MyEnergi.get_tank_id("MIDDLE")

    def test_empty_string_raises(self):
        with pytest.raises(Exception):
            MyEnergi.get_tank_id("")


# ---------------------------------------------------------------------------
# _get_day_of_week_string
# ---------------------------------------------------------------------------

class TestGetDayOfWeekString:
    @pytest.mark.parametrize("day,expected", [
        (0, "01000000"),  # Monday
        (1, "00100000"),  # Tuesday
        (2, "00010000"),  # Wednesday
        (3, "00001000"),  # Thursday
        (4, "00000100"),  # Friday
        (5, "00000010"),  # Saturday
        (6, "00000001"),  # Sunday
    ])
    def test_valid_days(self, me, day, expected):
        assert me._get_day_of_week_string(day) == expected

    def test_invalid_day_raises(self, me):
        with pytest.raises(Exception):
            me._get_day_of_week_string(7)

    def test_negative_day_raises(self, me):
        with pytest.raises(Exception):
            me._get_day_of_week_string(-1)


# ---------------------------------------------------------------------------
# _is_valid_boost_dict
# ---------------------------------------------------------------------------

class TestIsValidBoostDict:
    def _full_boost_dict(self):
        return {
            'bdd': '01111110',
            'bdh': 1,
            'bdm': 30,
            'bsh': 7,
            'bsm': 0,
            'slt': 11,
        }

    def test_valid_dict(self, me):
        assert me._is_valid_boost_dict(self._full_boost_dict()) is True

    def test_missing_one_key(self, me):
        d = self._full_boost_dict()
        del d['slt']
        assert me._is_valid_boost_dict(d) is False

    def test_empty_dict(self, me):
        assert me._is_valid_boost_dict({}) is False

    def test_extra_keys_still_valid(self, me):
        d = self._full_boost_dict()
        d['extra'] = 'irrelevant'
        assert me._is_valid_boost_dict(d) is True


# ---------------------------------------------------------------------------
# _get_sched_day_list
# ---------------------------------------------------------------------------

class TestGetSchedDayList:
    def test_monday_only(self, me):
        result = me._get_sched_day_list("01000000")
        assert result == "Mon"

    def test_tuesday_only(self, me):
        result = me._get_sched_day_list("00100000")
        assert result == "Tue"

    def test_sunday_only(self, me):
        result = me._get_sched_day_list("00000001")
        assert result == "Sun"

    def test_no_days_set(self, me):
        result = me._get_sched_day_list("00000000")
        assert result == ""

    def test_wrong_length_returns_empty(self, me):
        # bdd that is not 8 characters should yield empty list
        result = me._get_sched_day_list("010")
        assert result == ""

    def test_multiple_days_all_returned(self, me):
        # All if checks are independent so Mon AND Tue are both returned
        result = me._get_sched_day_list("01100000")
        assert result == "Mon,Tue"

    def test_all_weekdays(self, me):
        result = me._get_sched_day_list("01111100")
        assert result == "Mon,Tue,Wed,Thu,Fri"

    def test_weekend_only(self, me):
        result = me._get_sched_day_list("00000011")
        assert result == "Sat,Sun"

    def test_every_day_of_week(self, me):
        result = me._get_sched_day_list("01111111")
        assert result == "Mon,Tue,Wed,Thu,Fri,Sat,Sun"

    @pytest.mark.parametrize("day_bit,expected", [
        ("01000000", "Mon"),
        ("00100000", "Tue"),
        ("00010000", "Wed"),
        ("00001000", "Thu"),
        ("00000100", "Fri"),
        ("00000010", "Sat"),
        ("00000001", "Sun"),
    ])
    def test_each_individual_day(self, me, day_bit, expected):
        assert me._get_sched_day_list(day_bit) == expected


# ---------------------------------------------------------------------------
# _get_sched_table_row
# ---------------------------------------------------------------------------

class TestGetSchedTableRow:
    def test_basic_row(self, me):
        row = me._get_sched_table_row("01000000", 2, 30, 7, 0)
        assert row[0] == "07:00"   # start_time
        assert row[1] == "02:30"   # duration
        assert "Mon" in row[2]

    def test_midnight_start(self, me):
        row = me._get_sched_table_row("00000001", 0, 0, 0, 0)
        assert row[0] == "00:00"
        assert row[1] == "00:00"


# ---------------------------------------------------------------------------
# _exec_api_cmd
# ---------------------------------------------------------------------------

class TestExecApiCmd:
    @patch("eddi_and_iog.myenergi.requests.get")
    def test_successful_response(self, mock_get, me):
        payload = [{"key": "value"}]
        mock_get.return_value = make_mock_response(payload)
        result = me._exec_api_cmd(MyEnergi.BASE_URL + "cgi-jstatus-*")
        assert result == payload

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_non_200_raises(self, mock_get, me):
        mock_get.return_value = make_mock_response({}, status_code=401)
        with pytest.raises(Exception, match="401"):
            me._exec_api_cmd(MyEnergi.BASE_URL + "cgi-jstatus-*")

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_non_zero_status_in_body_raises(self, mock_get, me):
        mock_get.return_value = make_mock_response({"status": -1})
        with pytest.raises(Exception, match="status code returned from myenergi server"):
            me._exec_api_cmd(MyEnergi.BASE_URL + "cgi-jstatus-*")

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_zero_status_in_body_ok(self, mock_get, me):
        payload = {"status": 0, "data": 42}
        mock_get.return_value = make_mock_response(payload)
        result = me._exec_api_cmd(MyEnergi.BASE_URL + "cgi-jstatus-*")
        assert result == payload

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_uses_digest_auth(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        me._exec_api_cmd(MyEnergi.BASE_URL + "test")
        _, kwargs = mock_get.call_args
        from requests.auth import HTTPDigestAuth
        assert isinstance(kwargs["auth"], HTTPDigestAuth)


# ---------------------------------------------------------------------------
# update_stats / _get_eddi_stat / _get_zappi_stat
# ---------------------------------------------------------------------------

SAMPLE_STATS = [
    {"eddi": [{"sno": EDDI_SN, "tp1": 55, "tp2": 40, "ectp1": 3000, "hno": 1}]},
    {"zappi": [{"sno": ZAPPI_SN, "zmo": 3, "ectp1": 7200, "pst": "C2", "che": 12.5}]},
]


class TestUpdateStats:
    @patch("eddi_and_iog.myenergi.requests.get")
    def test_update_stats_populates_eddi(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        me.update_stats()
        assert me._eddi_stats_dict["tp1"] == 55

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_update_stats_populates_zappi(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        me.update_stats()
        assert me._zappi_stats_dict["zmo"] == 3

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_update_stats_ignores_wrong_serial(self, mock_get, me):
        wrong_stats = [
            {"eddi": [{"sno": "99999999", "tp1": 55}]},
        ]
        mock_get.return_value = make_mock_response(wrong_stats)
        me.update_stats()
        assert me._eddi_stats_dict is None

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_eddi_stat_returns_value(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me._get_eddi_stat("tp1") == 55

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_eddi_stat_missing_throws(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        with pytest.raises(Exception, match="Failed to read myenergi eddi"):
            me._get_eddi_stat("nonexistent_key")

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_eddi_stat_missing_no_throw(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        result = me._get_eddi_stat("nonexistent_key", throw_error=False)
        assert result is None

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_zappi_stat_returns_value(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me._get_zappi_stat("che") == 12.5

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_zappi_stat_missing_throws(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        with pytest.raises(Exception, match="Failed to read myenergi zappi"):
            me._get_zappi_stat("nonexistent_key")


# ---------------------------------------------------------------------------
# Eddi temperature / heater helpers
# ---------------------------------------------------------------------------

class TestEddiStats:
    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_eddi_top_tank_temp(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me.get_eddi_top_tank_temp() == 55

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_eddi_bottom_tank_temp(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me.get_eddi_bottom_tank_temp() == 40

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_eddi_heater_watts(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me.get_eddi_heater_watts() == 3000

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_eddi_heater_number(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me.get_eddi_heater_number() == 1


# ---------------------------------------------------------------------------
# Zappi stat helpers
# ---------------------------------------------------------------------------

class TestZappiStats:
    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_zappi_charge_mode(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me.get_zappi_charge_mode() == 3

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_zappi_charge_watts(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me.get_zappi_charge_watts() == 7200

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_zappi_plug_status(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me.get_zappi_plug_status() == "C2"

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_get_zappi_ev_charge_kwh(self, mock_get, me):
        mock_get.return_value = make_mock_response(SAMPLE_STATS)
        assert me.get_zappi_ev_charge_kwh() == 12.5


# ---------------------------------------------------------------------------
# set_boost
# ---------------------------------------------------------------------------

class TestSetBoost:
    @patch("eddi_and_iog.myenergi.requests.get")
    def test_boost_on_relay_1(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        me.set_boost(on=True, mins=30, relay=1)
        url_called = mock_get.call_args[0][0]
        assert f"cgi-eddi-boost-E{EDDI_SN}-10-1-30" in url_called

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_boost_on_relay_2(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        me.set_boost(on=True, mins=60, relay=2)
        url_called = mock_get.call_args[0][0]
        assert f"cgi-eddi-boost-E{EDDI_SN}-10-2-60" in url_called

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_boost_on_invalid_relay_raises(self, mock_get, me):
        with pytest.raises(Exception, match="switch must be 1 or 2"):
            me.set_boost(on=True, mins=30, relay=3)

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_boost_off_sends_two_requests(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        me.set_boost(on=False, mins=0)
        assert mock_get.call_count == 2

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_boost_off_url_format(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        me.set_boost(on=False, mins=0)
        urls = [call[0][0] for call in mock_get.call_args_list]
        assert any(f"cgi-eddi-boost-E{EDDI_SN}-1-1-0" in u for u in urls)
        assert any(f"cgi-eddi-boost-E{EDDI_SN}-1-2-0" in u for u in urls)


# ---------------------------------------------------------------------------
# _get_eddi_schedule_string
# ---------------------------------------------------------------------------

class TestGetEddiScheduleString:
    def _monday_noon(self):
        # 2024-01-01 is a Monday
        return datetime(2024, 1, 1, 12, 0)

    def test_schedule_on_top_tank(self, me):
        dt = self._monday_noon()
        delta = timedelta(hours=1, minutes=30)
        result = me._get_eddi_schedule_string(True, dt, delta, MyEnergi.TOP_TANK_ID)
        # slot 14 for top tank
        assert result.startswith("14-")
        assert "1200" in result  # start time
        assert "01000000" in result  # Monday

    def test_schedule_on_bottom_tank(self, me):
        dt = self._monday_noon()
        delta = timedelta(hours=2)
        result = me._get_eddi_schedule_string(True, dt, delta, MyEnergi.BOTTOM_TANK_ID)
        assert result.startswith("24-")

    def test_schedule_off_format(self, me):
        result = me._get_eddi_schedule_string(False, None, None, MyEnergi.TOP_TANK_ID)
        assert result == "14-0000-000-00000000"

    def test_invalid_tank_raises(self, me):
        dt = self._monday_noon()
        with pytest.raises(Exception, match="invalid water tank"):
            me._get_eddi_schedule_string(True, dt, timedelta(hours=1), 99)

    def test_duration_string_format(self, me):
        dt = self._monday_noon()
        delta = timedelta(hours=3, minutes=5)
        result = me._get_eddi_schedule_string(True, dt, delta, MyEnergi.TOP_TANK_ID)
        # duration should be "305"
        assert "305" in result


# ---------------------------------------------------------------------------
# _get_zappi_charge_string
# ---------------------------------------------------------------------------

class TestGetZappiChargeString:
    def _monday_slot(self):
        return {
            MyEnergi.SLOT_START_DATETIME: datetime(2024, 1, 1, 8, 0),   # 08:00 Mon
            MyEnergi.SLOT_STOP_DATETIME:  datetime(2024, 1, 1, 10, 0),  # 10:00 Mon
        }

    def test_valid_slot(self, me):
        result = me._get_zappi_charge_string(self._monday_slot(), 11)
        assert result.startswith("11-")
        assert "0800" in result
        assert "01000000" in result  # Monday

    def test_invalid_slot_raises(self, me):
        with pytest.raises(Exception, match="invalid slot id"):
            me._get_zappi_charge_string(self._monday_slot(), 99)

    def test_duration_over_9_hours_raises(self, me):
        slot = {
            MyEnergi.SLOT_START_DATETIME: datetime(2024, 1, 1, 0, 0),
            MyEnergi.SLOT_STOP_DATETIME:  datetime(2024, 1, 1, 10, 0),  # 10 hours
        }
        with pytest.raises(Exception, match="less than 9 hours"):
            me._get_zappi_charge_string(slot, 11)

    def test_duration_string_format(self, me):
        slot = {
            MyEnergi.SLOT_START_DATETIME: datetime(2024, 1, 1, 6, 0),
            MyEnergi.SLOT_STOP_DATETIME:  datetime(2024, 1, 1, 7, 45),  # 1h45m
        }
        result = me._get_zappi_charge_string(slot, 11)
        assert "145" in result  # 1h 45m


# ---------------------------------------------------------------------------
# set_zappi_charge_schedule
# ---------------------------------------------------------------------------

class TestSetZappiChargeSchedule:
    @patch("eddi_and_iog.myenergi.requests.get")
    def test_too_many_schedules_raises(self, mock_get, me):
        slots = [
            {
                MyEnergi.SLOT_START_DATETIME: datetime(2024, 1, 1, 8, 0),
                MyEnergi.SLOT_STOP_DATETIME:  datetime(2024, 1, 1, 9, 0),
            }
        ] * 5  # 5 slots — too many
        with pytest.raises(Exception, match="only 4 schedules"):
            me.set_zappi_charge_schedule(slots)

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_single_schedule_calls_api(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        slots = [{
            MyEnergi.SLOT_START_DATETIME: datetime(2024, 1, 1, 8, 0),
            MyEnergi.SLOT_STOP_DATETIME:  datetime(2024, 1, 1, 9, 0),
        }]
        me.set_zappi_charge_schedule(slots)
        assert mock_get.call_count == 1
        url = mock_get.call_args[0][0]
        assert f"cgi-boost-time-Z{ZAPPI_SN}" in url

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_four_schedules_makes_four_calls(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        slot = {
            MyEnergi.SLOT_START_DATETIME: datetime(2024, 1, 1, 8, 0),
            MyEnergi.SLOT_STOP_DATETIME:  datetime(2024, 1, 1, 9, 0),
        }
        me.set_zappi_charge_schedule([slot] * 4)
        assert mock_get.call_count == 4


# ---------------------------------------------------------------------------
# Zappi mode commands
# ---------------------------------------------------------------------------

class TestZappiModeCommands:
    @patch("eddi_and_iog.myenergi.requests.get")
    def test_fast_charge_url(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        me.set_zappi_mode_fast_charge()
        url = mock_get.call_args[0][0]
        assert f"cgi-zappi-mode-Z{ZAPPI_SN}-1-0-0-0000" in url

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_eco_url(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        me.set_zappi_mode_eco()
        url = mock_get.call_args[0][0]
        assert f"cgi-zappi-mode-Z{ZAPPI_SN}-2-0-0-0000" in url

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_eco_plus_url(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        me.set_zappi_mode_eco_plus()
        url = mock_get.call_args[0][0]
        assert f"cgi-zappi-mode-Z{ZAPPI_SN}-3-0-0-0000" in url

    @patch("eddi_and_iog.myenergi.requests.get")
    def test_stop_url(self, mock_get, me):
        mock_get.return_value = make_mock_response([])
        me.set_zappi_mode_stop()
        url = mock_get.call_args[0][0]
        assert f"cgi-zappi-mode-Z{ZAPPI_SN}-4-0-0-0000" in url


# ---------------------------------------------------------------------------
# set_all_zappi_schedules_off
# ---------------------------------------------------------------------------

class TestSetAllZappiSchedulesOff:
    @patch("eddi_and_iog.myenergi.sleep", return_value=None)   # skip real sleep
    @patch("eddi_and_iog.myenergi.requests.get")
    def test_calls_api_for_each_slot(self, mock_get, mock_sleep, me):
        mock_get.return_value = make_mock_response([])
        me.set_all_zappi_schedules_off()
        assert mock_get.call_count == len(MyEnergi.VALID_ZAPPI_SLOT_ID_LIST)

    @patch("eddi_and_iog.myenergi.sleep", return_value=None)
    @patch("eddi_and_iog.myenergi.requests.get")
    def test_urls_contain_zero_schedule(self, mock_get, mock_sleep, me):
        mock_get.return_value = make_mock_response([])
        me.set_all_zappi_schedules_off()
        for call in mock_get.call_args_list:
            assert "0000-000-00000000" in call[0][0]


# ---------------------------------------------------------------------------
# get_zappi_schedule_list
# ---------------------------------------------------------------------------

class TestGetZappiScheduleList:
    @patch.object(MyEnergi, "get_zappi_stats")
    def test_returns_empty_when_no_boost_times(self, mock_stats, me):
        mock_stats.return_value = {}
        result = me.get_zappi_schedule_list()
        assert result == []

    @patch.object(MyEnergi, "get_zappi_stats")
    def test_returns_row_for_valid_boost(self, mock_stats, me):
        mock_stats.return_value = {
            "boost_times": [
                {"bdd": "01000000", "bdh": 1, "bdm": 0, "bsh": 7, "bsm": 30, "slt": 11}
            ]
        }
        result = me.get_zappi_schedule_list()
        assert len(result) == 1
        start, duration, days = result[0]
        assert start == "07:30"
        assert duration == "01:00"
        assert "Mon" in days

    @patch.object(MyEnergi, "get_zappi_stats")
    def test_skips_invalid_boost_dict(self, mock_stats, me):
        mock_stats.return_value = {
            "boost_times": [
                {"bdd": "01000000", "bdh": 1}   # missing keys
            ]
        }
        result = me.get_zappi_schedule_list()
        assert result == []
