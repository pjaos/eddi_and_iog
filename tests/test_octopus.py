"""
test_eddi_and_iog.py
=====================
Tests for eddi_and_iog.py.

Run with:
    poetry run pytest -v tests/test_eddi_and_iog.py
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from eddi_and_iog.octopus import OctopusClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc(hour: int, minute: int) -> datetime:
    """Return a UTC-aware datetime for today at the given hour:minute."""
    return datetime.now(timezone.utc).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def make_client() -> OctopusClient:
    """Return an OctopusClient with default off-peak window (23:30-05:30)."""
    return OctopusClient(api_key="test_key", account_number="A-TEST1234")


def make_dispatch(start: datetime, end: datetime) -> dict:
    """Return a raw dispatch dict using flexPlannedDispatches field names."""
    return {"start": start.isoformat(), "end": end.isoformat()}


FULL_SCHEDULE_STRING = (
    "50,60,"
    "23:30-05:30,00:00-00:00,1,0,"
    "00:00-00:00,00:00-00:00,0,0,"
    "00:00-00:00,00:00-00:00,0,0"
)

TOKEN_RESPONSE = {
    "data": {"obtainKrakenToken": {"token": "test-token-abc123"}}
}

DEVICES_RESPONSE = {
    "data": {
        "devices": [
            {"id": "device-ev-001",    "deviceType": "ELECTRIC_VEHICLES"},
            {"id": "device-meter-001", "deviceType": "ELECTRICITY_METERS"},
        ]
    }
}


def mock_response(response_json: dict, status_code: int = 200) -> MagicMock:
    """Return a mock requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = response_json
    mock_resp.text = str(response_json)
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ===========================================================================
# OctopusClient._parse_dt
# ===========================================================================

class TestParseDt:
    def test_parses_utc_iso_string(self):
        dt = OctopusClient._parse_dt("2024-01-15T02:00:00+00:00")
        assert dt.hour == 2
        assert dt.tzinfo is not None

    def test_parses_naive_string_assumes_utc(self):
        dt = OctopusClient._parse_dt("2024-01-15T02:00:00")
        assert dt.tzinfo == timezone.utc

    def test_parses_offset_aware_string(self):
        dt = OctopusClient._parse_dt("2024-01-15T03:00:00+01:00")
        assert dt.tzinfo is not None

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            OctopusClient._parse_dt("not-a-date")


# ===========================================================================
# OctopusClient._get_token
# ===========================================================================

class TestGetToken:

    def setup_method(self):
        self.client = make_client()

    def test_returns_token_on_success(self):
        with patch("eddi_and_iog.octopus.requests.post",
                   return_value=mock_response(TOKEN_RESPONSE)):
            token = self.client._get_token()
        assert token == "test-token-abc123"

    def test_caches_token_on_second_call(self):
        with patch("eddi_and_iog.octopus.requests.post",
                   return_value=mock_response(TOKEN_RESPONSE)) as mock_post:
            self.client._get_token()
            self.client._get_token()
        assert mock_post.call_count == 1

    def test_returns_none_when_token_missing_in_response(self):
        resp = {"data": {"obtainKrakenToken": {"token": None}}}
        with patch("eddi_and_iog.octopus.requests.post",
                   return_value=mock_response(resp)):
            token = self.client._get_token()
        assert token is None

    def test_returns_none_on_request_exception(self):
        with patch("eddi_and_iog.octopus.requests.post",
                   side_effect=Exception("network error")):
            token = self.client._get_token()
        assert token is None

    def test_uses_cached_token_without_api_call(self):
        self.client._token = "cached-token"
        with patch("eddi_and_iog.octopus.requests.post") as mock_post:
            token = self.client._get_token()
        assert token == "cached-token"
        mock_post.assert_not_called()


# ===========================================================================
# OctopusClient._get_device_id
# ===========================================================================

class TestGetDeviceId:

    def setup_method(self):
        self.client = make_client()
        self.client._token = "cached-token"

    def test_returns_ev_device_id(self):
        with patch("eddi_and_iog.octopus.requests.post",
                   return_value=mock_response(DEVICES_RESPONSE)):
            device_id = self.client._get_device_id()
        assert device_id == "device-ev-001"

    def test_skips_non_ev_devices(self):
        response = {
            "data": {
                "devices": [
                    {"id": "device-meter-001", "deviceType": "ELECTRICITY_METERS"},
                    {"id": "device-ev-001",    "deviceType": "ELECTRIC_VEHICLES"},
                ]
            }
        }
        with patch("eddi_and_iog.octopus.requests.post",
                   return_value=mock_response(response)):
            device_id = self.client._get_device_id()
        assert device_id == "device-ev-001"

    def test_returns_none_when_no_ev_device(self):
        response = {
            "data": {
                "devices": [
                    {"id": "device-meter-001", "deviceType": "ELECTRICITY_METERS"},
                ]
            }
        }
        with patch("eddi_and_iog.octopus.requests.post",
                   return_value=mock_response(response)):
            device_id = self.client._get_device_id()
        assert device_id is None

    def test_returns_none_when_no_token(self):
        self.client._token = None
        with patch.object(self.client, "_get_token", return_value=None):
            device_id = self.client._get_device_id()
        assert device_id is None

    def test_caches_device_id(self):
        with patch("eddi_and_iog.octopus.requests.post",
                   return_value=mock_response(DEVICES_RESPONSE)) as mock_post:
            self.client._get_device_id()
            self.client._get_device_id()
        assert mock_post.call_count == 1

    def test_uses_cached_device_id_without_api_call(self):
        self.client._device_id = "cached-device"
        with patch("eddi_and_iog.octopus.requests.post") as mock_post:
            device_id = self.client._get_device_id()
        assert device_id == "cached-device"
        mock_post.assert_not_called()

    def test_returns_none_on_request_exception(self):
        with patch("eddi_and_iog.octopus.requests.post",
                   side_effect=Exception("network error")):
            device_id = self.client._get_device_id()
        assert device_id is None


# ===========================================================================
# OctopusClient._is_token_expired
# ===========================================================================

class TestIsTokenExpired:

    def setup_method(self):
        self.client = make_client()

    def test_returns_true_for_kt_ct_1124(self):
        data = {"errors": [{"extensions": {"errorCode": "KT-CT-1124"}}]}
        assert self.client._is_token_expired(data) is True

    def test_returns_false_for_other_error_code(self):
        data = {"errors": [{"extensions": {"errorCode": "KT-CT-9999"}}]}
        assert self.client._is_token_expired(data) is False

    def test_returns_false_for_no_errors(self):
        assert self.client._is_token_expired({"data": {}}) is False

    def test_returns_false_for_empty_errors(self):
        assert self.client._is_token_expired({"errors": []}) is False


# ===========================================================================
# OctopusClient._get_planned_dispatches
# ===========================================================================

class TestGetPlannedDispatches:

    def setup_method(self):
        self.client = make_client()
        self.client._token     = "cached-token"
        self.client._device_id = "device-ev-001"

    def _dispatch_response(self, dispatches: list) -> dict:
        return {"data": {"flexPlannedDispatches": dispatches}}

    def test_returns_empty_list_when_no_dispatches(self):
        with patch("eddi_and_iog.octopus.requests.post",
                   return_value=mock_response(self._dispatch_response([]))):
            result = self.client._get_planned_dispatches()
        assert result == []

    def test_returns_dispatches(self):
        now = datetime.now(timezone.utc)
        dispatches = [
            {"start": now.isoformat(), "end": (now + timedelta(hours=1)).isoformat()}
        ]
        with patch("eddi_and_iog.octopus.requests.post",
                   return_value=mock_response(self._dispatch_response(dispatches))):
            result = self.client._get_planned_dispatches()
        assert len(result) == 1

    def test_returns_empty_when_no_token(self):
        self.client._token = None
        with patch.object(self.client, "_get_token", return_value=None):
            result = self.client._get_planned_dispatches()
        assert result == []

    def test_returns_empty_when_no_device_id(self):
        self.client._device_id = None
        with patch.object(self.client, "_get_device_id", return_value=None):
            result = self.client._get_planned_dispatches()
        assert result == []

    def test_clears_token_on_exception(self):
        self.client._token = "valid-token"
        with patch("eddi_and_iog.octopus.requests.post",
                   side_effect=Exception("network error")):
            result = self.client._get_planned_dispatches()
        assert result == []
        assert self.client._token is None

    def test_refreshes_token_on_jwt_expiry_in_body(self):
        expired_response = {
            "errors": [{"extensions": {"errorCode": "KT-CT-1124"}}],
            "data":   {"flexPlannedDispatches": None},
        }
        now = datetime.now(timezone.utc)
        fresh_dispatches = [
            {"start": now.isoformat(), "end": (now + timedelta(hours=1)).isoformat()}
        ]
        fresh_response = self._dispatch_response(fresh_dispatches)

        responses = iter([expired_response, fresh_response])
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.text = ""
        mock_resp.json.side_effect = lambda: next(responses)

        with patch("eddi_and_iog.octopus.requests.post", return_value=mock_resp):
            with patch.object(self.client, "_get_token", return_value="new-token"):
                self.client._token = "old-token"
                result = self.client._get_planned_dispatches()

        assert len(result) == 1


# ===========================================================================
# OctopusClient._is_outside_offpeak
# ===========================================================================

class TestIsOutsideOffpeak:

    def setup_method(self):
        self.client = make_client()

    def test_exact_offpeak_window_is_not_outside(self):
        assert self.client._is_outside_offpeak(utc(23, 30), utc(5, 30)) is False

    def test_slot_wholly_within_offpeak_after_midnight(self):
        assert self.client._is_outside_offpeak(utc(0, 0), utc(5, 0)) is False

    def test_slot_starts_at_offpeak_start(self):
        assert self.client._is_outside_offpeak(utc(23, 30), utc(4, 0)) is False

    def test_slot_ends_at_offpeak_end(self):
        assert self.client._is_outside_offpeak(utc(1, 0), utc(5, 30)) is False

    def test_slot_starts_before_offpeak(self):
        assert self.client._is_outside_offpeak(utc(23, 0), utc(5, 30)) is True

    def test_slot_ends_after_offpeak(self):
        assert self.client._is_outside_offpeak(utc(23, 30), utc(6, 0)) is True

    def test_slot_spans_beyond_offpeak_on_both_sides(self):
        assert self.client._is_outside_offpeak(utc(23, 0), utc(6, 0)) is True

    def test_daytime_slot_is_outside(self):
        assert self.client._is_outside_offpeak(utc(12, 0), utc(13, 0)) is True

    def test_early_morning_slot_past_offpeak_end(self):
        assert self.client._is_outside_offpeak(utc(5, 30), utc(7, 0)) is True

    def test_evening_slot_before_offpeak_start(self):
        assert self.client._is_outside_offpeak(utc(20, 0), utc(22, 0)) is True

    def test_custom_offpeak_window(self):
        client = OctopusClient(
            api_key="k", account_number="A-TEST",
            offpeak_start=(22, 0), offpeak_end=(6, 0),
        )
        assert client._is_outside_offpeak(utc(22, 0), utc(6, 0)) is False
        assert client._is_outside_offpeak(utc(21, 0), utc(6, 0)) is True


# ===========================================================================
# OctopusClient.find_active_extra_dispatch
# ===========================================================================

class TestFindActiveExtraDispatch:

    def setup_method(self):
        self.client = make_client()
        self.client._token     = "cached-token"
        self.client._device_id = "device-ev-001"

    def _patch_dispatches(self, dispatches: list[dict]):
        self.client._get_planned_dispatches = MagicMock(return_value=dispatches)

    def test_returns_none_when_no_dispatches(self):
        self._patch_dispatches([])
        assert self.client.find_active_extra_dispatch() is None

    def test_returns_none_for_future_dispatch(self):
        now = datetime.now(timezone.utc)
        self._patch_dispatches([make_dispatch(now + timedelta(hours=2),
                                              now + timedelta(hours=3))])
        assert self.client.find_active_extra_dispatch() is None

    def test_returns_none_for_past_dispatch(self):
        now = datetime.now(timezone.utc)
        self._patch_dispatches([make_dispatch(now - timedelta(hours=3),
                                              now - timedelta(hours=1))])
        assert self.client.find_active_extra_dispatch() is None

    def test_returns_none_for_active_in_window_dispatch(self):
        now   = datetime.now(timezone.utc)
        start = now.replace(hour=23, minute=30, second=0, microsecond=0)
        if start > now:
            start -= timedelta(days=1)
        end = start + timedelta(hours=4)
        self._patch_dispatches([make_dispatch(start, end)])
        assert self.client.find_active_extra_dispatch() is None

    def test_skips_malformed_dispatch(self):
        self._patch_dispatches([{"bad": "data"}])
        assert self.client.find_active_extra_dispatch() is None

    def test_skips_malformed_and_continues(self):
        self._patch_dispatches([{"bad": "data"}, {"also": "bad"}])
        result = self.client.find_active_extra_dispatch()
        assert result is None

# TODO: Add tests to check EddiSyncApp