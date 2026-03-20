import json
import threading
from time import sleep
import requests
from requests.auth import HTTPDigestAuth

class MyEnergi(object):
    """@brief An interface to MyEnergi products.
              This is not meant to be a comprehensive interface.
              It provides the functionality required by this application."""
    TANK_TOP = 1
    TANK_BOTTOM = 2
    TANK_STOP_STR = "TOP"
    TANK_BOTTOM_STR = "BOTTOM"
    BASE_URL = 'https://s18.myenergi.net/'
    TOP_TANK_ID = 1
    BOTTOM_TANK_ID = 2
    TANK_1_BOOST_SCHEDULE_SLOT_ID = 14
    TANK_2_BOOST_SCHEDULE_SLOT_ID = 24
    VALID_EDDI_SLOT_ID_LIST = (11, 12, 13, TANK_1_BOOST_SCHEDULE_SLOT_ID, 21, 22, 23, TANK_2_BOOST_SCHEDULE_SLOT_ID)
    VALID_ZAPPI_SLOT_ID_LIST = (11, 12, 13, 14)
    ZAPPI_CHARG_MODE_FAST = 1
    ZAPPI_CHARGE_MODE_ECO = 2
    ZAPPI_CHARGE_MODE_ECO_PLUS = 3
    ZAPPI_CHARGE_MODE_STOPPED = 4

    ZAPPI_PST_EV_DISCONNECTED = 'A'
    ZAPPI_PST_EV_CONNECTED = 'B1'
    ZAPPI_PST_WAITING_FOR_EV = 'B2'
    ZAPPI_PST_EV_READY_TO_CHARGE = 'C1'
    ZAPPI_PST_EV_CHARGING = 'C2'
    ZAPPI_PST_FAULT = 'F'

    ZAPPI_STA_EVSE_READY = 1
    ZAPPI_STA_CONNECTED = 2
    ZAPPI_STA_CHARGING = 3
    ZAPPI_STA_WAITING = 4
    ZAPPI_STA_BOOSTING = 5

    BOOST_TIMES_KEY = "boost_times"
    BOOST_TIMES_KEY = "boost_times"
    BDD_BOOST_DICT_KEY = 'bdd'
    BDH_BOOST_DICT_KEY = 'bdh'
    BDM_BOOST_DICT_KEY = 'bdm'
    BSH_BOOST_DICT_KEY = 'bsh'
    BSM_BOOST_DICT_KEY = 'bsm'
    SLT_BOOST_DICT_KEY = 'slt'

    BOOST_DICT_KEYS = [BDD_BOOST_DICT_KEY,
                       BDH_BOOST_DICT_KEY,
                       BDM_BOOST_DICT_KEY,
                       BSH_BOOST_DICT_KEY,
                       BSM_BOOST_DICT_KEY,
                       SLT_BOOST_DICT_KEY
                       ]

    SLOT_START_DATETIME = "SLOT_START_DATETIME"
    SLOT_STOP_DATETIME = "SLOT_STOP_DATETIME"

    @staticmethod
    def get_tank_id(tank_str):
        """@brief Get the tank ID given a string, either TOP or BOTTOM."""
        tank = -1
        tank_str = tank_str.upper()
        if tank_str == MyEnergi.TANK_STOP_STR:
            tank = MyEnergi.TANK_TOP

        elif tank_str == MyEnergi.TANK_BOTTOM_STR:
            tank = MyEnergi.TANK_BOTTOM

        else:
            raise Exception("{tank} is an unknown EDDI tank (TOP or BOTTOM are valid).")

        return tank


    def __init__(self, api_key: str, uio=None):
        """@brief Constuctor
           @param api_key Your myenergi API key.
                          You must create this on the myenergi web site.
                          See https://support.myenergi.com/hc/en-gb/articles/5069627351185-How-do-I-get-an-API-key for more information.
           @param uio An UIO instance."""
        self._api_key = api_key
        self._eddi_serial_number = ""
        self._zappi_serial_number = ""
        self._eddi_stats_dict = None
        self._zappi_stats_dict = None
        self._uio = uio
        self._lock = threading.Lock()

    def set_eddi_serial_number(self, eddi_serial_number):
        """@brief set the eddi serial number.
           @param eddi_serial_number The serial number of the eddi unit of interest."""
        self._eddi_serial_number = eddi_serial_number

    def get_eddi_serial_number(self):
        """@brief Return the EDDI serial number."""
        return self._eddi_serial_number

    def set_zappi_serial_number(self, zappi_serial_number):
        """@brief set the zappi serial number.
           @param zappi_serial_number The serial number of the zappi unit of interest."""
        self._zappi_serial_number = zappi_serial_number

    def _check_eddi_serial_number(self):
        """@brief Check that the eddi serial number has been set."""
        if self._eddi_serial_number is None:
            raise Exception("BUG: The eddi serial number has not been set.")

    def _check_zappi_serial_number(self):
        """@brief Check that the zappi serial number has been set."""
        if self._zappi_serial_number is None:
            raise Exception("BUG: The zappi serial number has not been set.")

    def get_stats(self):
        """@brief Get the stats of the eddi unit."""
        self._check_eddi_serial_number()
        url = MyEnergi.BASE_URL + "cgi-jstatus-*"
        return self._exec_api_cmd(url)

    def update_stats(self):
        """@brief update all the stats."""
        stats_list = self.get_stats()
        for stats_dict in stats_list:
            if 'eddi' in stats_dict:
                eddi_dict_list = stats_dict['eddi']
                for eddi_dict in eddi_dict_list:
                    if 'sno' in eddi_dict:
                        serial_number = eddi_dict['sno']
                        # Check the eddi serial number matches
                        if str(serial_number) == str(self._eddi_serial_number):
                            # Assign the eddi dict
                            self._eddi_stats_dict = eddi_dict

            elif 'zappi' in stats_dict:
                zappi_dict_list = stats_dict['zappi']
                for zappi_dict in zappi_dict_list:
                    if 'sno' in zappi_dict:
                        serial_number = zappi_dict['sno']
                        # Check the zappi serial number matches
                        if str(serial_number) == str(self._zappi_serial_number):
                            # Assign the zappi dict
                            self._zappi_stats_dict = zappi_dict

    def _get_eddi_stat(self, name, throw_error=True):
        """@brief Get a eddi stat after update_stats() has been called.
           @param name The name of the stat of interest.
           @param throw_error True if this method should throw an error if the stats is not found.
           @return The stat or None if not found."""
        stat = None
        # If the stats have not been read yet, read them
        if not self._eddi_stats_dict or name not in self._eddi_stats_dict:
            self.update_stats()

        if self._eddi_stats_dict:
            if name in self._eddi_stats_dict:
                stat = self._eddi_stats_dict[name]

        if throw_error and stat is None:
            raise Exception(f"Failed to read myenergi eddi '{name}={stat}'.")

        return stat

    def _get_zappi_stat(self, name, throw_error=True):
        """@brief Get a zappi stat after update_stats() has been called.
           @param name The name of the stat of interest.
           @param throw_error True if this method should throw an error if the stats is not found.
           @return The stat or None if not found."""
        stat = None
        # If the stats have not been read yet, read them
        if not self._zappi_stats_dict or name not in self._zappi_stats_dict:
            self.update_stats()

        if self._zappi_stats_dict:
            if name in self._zappi_stats_dict:
                stat = self._zappi_stats_dict[name]

        if throw_error and stat is None:
            raise Exception(f"Failed to read myenergi zappi '{name}={stat}'.")

        return stat

    def get_eddi_top_tank_temp(self):
        """@return The eddi top tank temperature or None if not known."""
        return self._get_eddi_stat('tp1')

    def get_eddi_bottom_tank_temp(self):
        """@return The eddi bottom tank temperature or None if not known."""
        return self._get_eddi_stat('tp2')

    def get_eddi_heater_watts(self):
        """@return The eddi heater power in kw or None if not known."""
        return self._get_eddi_stat('ectp1')

    def get_eddi_heater_number(self):
        """@return The eddi heater number that is on.
                   If no heater is on then this stays at the last value.
                   1 = top tank, 2 = bottom tank"""
        return self._get_eddi_stat('hno')

    def get_zappi_charge_mode(self):
        """@return The zappi charge mode or None if not known."""
        return self._get_zappi_stat('zmo')

    def get_zappi_charge_watts(self):
        """@return Get the current charge rate of the zappi in watts."""
        return self._get_zappi_stat('ectp1')

    def get_zappi_plug_status(self):
        """@return Get the ZAPPI EV plug status.
           ZAPPI_PST_EV_DISCONNECTED
           ZAPPI_PST_EV_CONNECTED
           ZAPPI_PST_WAITING_FOR_EV
           ZAPPI_PST_EV_READY_TO_CHARGE
           ZAPPI_PST_EV_CHARGING
           ZAPPI_PST_FAULT
        """
        return self._get_zappi_stat('pst')

    def get_eddi_stats(self):
        """@brief Get the stats of the eddi unit."""
        self._check_eddi_serial_number()
        url = MyEnergi.BASE_URL + "cgi-jstatus-E"
        return self._exec_api_cmd(url)

    def get_zappi_schedule_list(self):
        """@brief Get the zappi charge schedule list.
           @return A list with four elements. Each element is a list
                   that contains the following three elements
                   0 = The time as HH:MM
                   1 = The duration as HH:MM
                   2 = A comma separated list of days of the week. Each day as three letters."""
        table_row_list = []
        zappi_stats_dict = self.get_zappi_stats()
        if MyEnergi.BOOST_TIMES_KEY in zappi_stats_dict:
            for boost_dict in zappi_stats_dict[MyEnergi.BOOST_TIMES_KEY]:
                if self._is_valid_boost_dict(boost_dict):
                    """A boost dict contains the following
                        0: bdd The days of the week in the form 01111111.
                            The first 1 indicates that the schedule applies to Mon
                            The next is Tue and so on until Sun.
                            Therefore 01111111 indicate the schedule applies to
                            every day of the week.
                        1: bdh Duration in hours.
                        2: bdm Duration in minutes.
                        3: bsh Time in hours.
                        4: bsm Time in minutes.
                        5: slt The slot. An integer to indicate the schedule slot (11,12,13 or 14)."""
                    bdd = boost_dict[MyEnergi.BDD_BOOST_DICT_KEY]
                    bdh = boost_dict[MyEnergi.BDH_BOOST_DICT_KEY]
                    bdm = boost_dict[MyEnergi.BDM_BOOST_DICT_KEY]
                    bsh = boost_dict[MyEnergi.BSH_BOOST_DICT_KEY]
                    bsm = boost_dict[MyEnergi.BSM_BOOST_DICT_KEY]
                    table_row = self._get_sched_table_row(bdd,
                                                          bdh,
                                                          bdm,
                                                          bsh,
                                                          bsm)
                    table_row_list.append(table_row)
        return table_row_list

    def _is_valid_boost_dict(self, boost_dict):
        """@brief Determine if the boost dict is valid.
           @return True if all the required keys are present in the boost dict."""
        key_count = 0
        for key in MyEnergi.BOOST_DICT_KEYS:
            if key in boost_dict:
                key_count = key_count + 1
        valid = False
        if key_count == 6:
            valid = True
        return valid

    def _get_sched_table_row(self,
                             bdd,
                             bdh,
                             bdm,
                             bsh,
                             bsm):
        """@return A list/row of values from the myenergi zappi charge schedules.
                   0 = start time (HH:MM)
                   1 = duration (HH:MM)
                   2 = Comma separated list of days of the week. Each day in three letter format."""
        day_list = self._get_sched_day_list(bdd)
        duration = f"{bdh:02d}:{bdm:02d}"
        start_time = f"{bsh:02d}:{bsm:02d}"
        table_row = None
        table_row = (start_time, duration, day_list)
        return table_row

    def _get_sched_day_list(self, bdd):
        """@brief Get a list of days that a schedule applies to.
           @param bdd The bdd field from the zappi schedule.
           @return A comma separated list of three letter day names."""
        day_list = []
        if len(bdd) == 8:
            if bdd[1] == '1':
                day_list.append('Mon')
            if bdd[2] == '1':
                day_list.append('Tue')
            if bdd[3] == '1':
                day_list.append('Wed')
            if bdd[4] == '1':
                day_list.append('Thu')
            if bdd[5] == '1':
                day_list.append('Fri')
            if bdd[6] == '1':
                day_list.append('Sat')
            if bdd[7] == '1':
                day_list.append('Sun')
        return ",".join(day_list)

    def get_zappi_stats(self):
        """@brief Get the stats of the zappi unit."""
        self._check_eddi_serial_number()
        self._check_zappi_serial_number()
        url = MyEnergi.BASE_URL + "cgi-boost-time-Z"+self._zappi_serial_number
        return self._exec_api_cmd(url)

    def set_boost(self, on, mins, relay=None):
        """@brief Set emersion switch on/off
           @param on True sets switch on. If False then switch does not need to be set as both switches are turned off.
           @param mins The number of minutes to boost for.
           @param relay  1 = Top tank heater.
                         2 = bottom tank heater.
                         """
        self._check_eddi_serial_number()
        if on:
            if relay not in (1, 2):
                raise Exception("BUG: set_boost() switch must be 1 or 2.")
            url = MyEnergi.BASE_URL + "cgi-eddi-boost-E"+self._eddi_serial_number+f"-10-{relay}-{mins}"
        else:
            url = MyEnergi.BASE_URL + "cgi-eddi-boost-E"+self._eddi_serial_number+"-1-1-0"
            self._exec_api_cmd(url)

            url = MyEnergi.BASE_URL + "cgi-eddi-boost-E"+self._eddi_serial_number+"-1-2-0"

        self._exec_api_cmd(url)

    def set_tank_schedule(self, on, on_datetime, duration_timedelta, tank):
        """@brief Set a schedule on the hot water tank.
           @param on If True add a schedule. If False delete a schedule.
           @param on_datetime A datetime instance that defines the on time for the tank heater.
           @param duration_timedelta A timedelta instance that defines ho long the tank heater should stay on.
           @param tank The hot water tank (1=top, 2 = bottom or 'TOP', 'BOTTOM')."""
        sched_sub_str = self._get_eddi_schedule_string(on, on_datetime, duration_timedelta, tank)
        url = MyEnergi.BASE_URL + f"cgi-boost-time-E{self._eddi_serial_number}-{sched_sub_str}"
        self._exec_api_cmd(url)

    def set_water_tank_boost_schedules_off(self):
        """@brief Set the boost tank water schedule off. We reserve the fourth schedule timer for this boost setting, leaving the other timers untouched.
                  Note, we use MyEnergi.TANK_1_BOOST_SCHEDULE_SLOT_ID and MyEnergi.TANK_2_BOOST_SCHEDULE_SLOT_ID schedules for boost purposes
                  rather than using the boost interface commands for the reason details in the _set_boost() method."""
        self.set_tank_schedule(False, None, None, MyEnergi.TOP_TANK_ID)
        self.set_tank_schedule(False, None, None, MyEnergi.BOTTOM_TANK_ID)

    def _get_eddi_schedule_string(self, on, on_datetime, duration_timedelta, tank):
        """@brief Get a timed schedule for a hot water tank.

            cgi-boost-time-E<eddi serial number>-<slot id>-<start time>-<duration>-<day spec>

                start time and duration are both numbers like 60*hours+minutes
                day spec is as bdd above'

            This method returns part of the above string as detailed below.

            '<slot id>-<start time>-<duration>-<day spec>'

           @param on_datetime A datetime instance that defines the on time for the tank heater.
           @param duration_timedelta A timedelta instance that defines ho long the tank heater should stay on.
           @param tank The hot water tank (1=top, 2 = bottom)."""

        self._check_eddi_serial_number()
        if tank not in [MyEnergi.TOP_TANK_ID, MyEnergi.BOTTOM_TANK_ID]:
            raise Exception(f"{tank} is an invalid water tank. Must be {MyEnergi.TOP_TANK_ID} (top) or {MyEnergi.BOTTOM_TANK_ID} (bottom).")

        if tank == 1:
            slot_id = MyEnergi.TANK_1_BOOST_SCHEDULE_SLOT_ID
        else:
            slot_id = MyEnergi.TANK_2_BOOST_SCHEDULE_SLOT_ID

        if on:
            on_time_string = f"{on_datetime.hour:02d}{on_datetime.minute:02d}"
            duration_hours, remainder = divmod(duration_timedelta.seconds, 3600)
            duration_minutes, _ = divmod(remainder, 60)
            duration_string = f"{duration_hours:01d}{duration_minutes:02d}"

            day_of_week = on_datetime.weekday()
            day_of_week_string = self._get_day_of_week_string(day_of_week)

            schedule_string = f"{slot_id:02d}-{on_time_string}-{duration_string}-{day_of_week_string}"

        else:
            schedule_string = f"{slot_id:02d}-0000-000-00000000"

        return schedule_string

    def set_all_zappi_schedules_off(self):
        """@brief Set all zappi charge schedules off.
                  We set charge schedules that have no on time and are not enabled for any days of the week.
                  This causes the 4 possible schedules on the zappi to be removed."""
        self._check_eddi_serial_number()
        self._check_zappi_serial_number()

        for slot_id in MyEnergi.VALID_ZAPPI_SLOT_ID_LIST:
            url = MyEnergi.BASE_URL + f"cgi-boost-time-Z{self._zappi_serial_number}-{slot_id}-0000-000-00000000"
            self._exec_api_cmd(url)
            # The myenergi system does not always delete the schedule unless a delay occurs between each command
            sleep(1)

    def _get_zappi_charge_string(self, charge_slot_dict, slot_id):
        """@detail Get a string that is formated as required by the myenergi zappi api.

            cgi-boost-time-Z<zappi serial number>-<slot id>-<start time>-<duration>-<day spec>

                start time and duration are both numbers like 60*hours+minutes
                day spec is as bdd above'

            This method returns part of the above string as detailed below.

            '<slot id>-<start time>-<duration>-<day spec>'

           @param charge_slot_dict The dict holding the start stop details of the charge.
           @param slot_id The slot ID (one of MyEnergi.VALID_ZAPPI_SLOT_ID_LIST).
        """
        if slot_id not in MyEnergi.VALID_ZAPPI_SLOT_ID_LIST:
            valid_list = ",".join([str(x) for x in MyEnergi.VALID_ZAPPI_SLOT_ID_LIST])
            raise Exception(f"{slot_id} is an invalid slot id (value = {valid_list})")

        start_datetime = charge_slot_dict[MyEnergi.SLOT_START_DATETIME]
        stop_datetime = charge_slot_dict[MyEnergi.SLOT_STOP_DATETIME]
        duration_timedelta = stop_datetime-start_datetime
        duration_hours, remainder = divmod(duration_timedelta.seconds, 3600)
        duration_minutes, _ = divmod(remainder, 60)
        day_of_week = start_datetime.weekday()  # where Monday is 0 and Sunday is 6

        # We cannot charge for more than 8 hours 59 mins
        if duration_hours > 9:
            raise Exception("The charge time must be less than 9 hours.")

        on_time_string = f"{start_datetime.hour:02d}{start_datetime.minute:02d}"
        duration_string = f"{duration_hours:01d}{duration_minutes:02d}"
        day_of_week_string = self._get_day_of_week_string(day_of_week)

        charge_string = f"{slot_id:02d}-{on_time_string}-{duration_string}-{day_of_week_string}"
        return charge_string

    def _get_day_of_week_string(self, day_of_week):
        """@brief Get the day of the week string used in the command sent to the myenergi server.
           @param day_of_week A single day of the week as an integer 0 - 6.
           @return The day of the week string in the format accepted by the myenergi server."""
        day_of_week_string = None
        if day_of_week == 0:
            day_of_week_string = "01000000"

        elif day_of_week == 1:
            day_of_week_string = "00100000"

        elif day_of_week == 2:
            day_of_week_string = "00010000"

        elif day_of_week == 3:
            day_of_week_string = "00001000"

        elif day_of_week == 4:
            day_of_week_string = "00000100"

        elif day_of_week == 5:
            day_of_week_string = "00000010"

        elif day_of_week == 6:
            day_of_week_string = "00000001"

        if day_of_week_string is None:
            raise Exception("{day_of_week} is an invalid day of the week. Must be 0-6")

        return day_of_week_string

    def _debug(self, msg):
        if self._uio:
            self._uio.debug(f"myenergi API DEBUG: {msg}")

    def _exec_api_cmd(self, url):
        """@brief Run a command using the myenergi api and check for errors.
           @return The json response message."""
        # As this maybe called from multiple threads ensure we use acquire a thread lock each time
        # we communicate with the myenergi server.
        with self._lock:
            self._debug(f"_exec_api_cmd: url={url}")
            response = requests.get(url, auth=HTTPDigestAuth(self._eddi_serial_number, self._api_key))
            if response.status_code != 200:
                raise Exception(f"{response.status_code} error code returned from myenergi server.")
            self._debug(f"_exec_api_cmd: response.status_code={response.status_code}")
            response_dict = response.json()

            if response_dict:
                index = 0
                for elem in response_dict:
                    pstr = json.dumps(elem, sort_keys=True, indent=4)
                    self._debug(f"_exec_api_cmd: index={index}, elem={pstr}")
                    index = index+1

                if 'status' in response_dict and response_dict['status'] != 0:
                    raise Exception(f"{response_dict['status']} status code returned from myenergi server (should be 0).")

        return response_dict

    def set_zappi_mode_fast_charge(self):
        """@brief Set the mode of the zappi charger to fast charge."""
        url = MyEnergi.BASE_URL + f"cgi-zappi-mode-Z{self._zappi_serial_number}-1-0-0-0000"
        self._exec_api_cmd(url)

    def set_zappi_mode_eco(self):
        """@brief Set the mode of the zappi charger to eco"""
        url = MyEnergi.BASE_URL + f"cgi-zappi-mode-Z{self._zappi_serial_number}-2-0-0-0000"
        self._exec_api_cmd(url)

    def set_zappi_mode_eco_plus(self):
        """@brief Set the mode of the zappi charger to eco+"""
        url = MyEnergi.BASE_URL + f"cgi-zappi-mode-Z{self._zappi_serial_number}-3-0-0-0000"
        self._exec_api_cmd(url)

    def set_zappi_mode_stop(self):
        """@brief Set the mode of the zappi charger to stop"""
        url = MyEnergi.BASE_URL + f"cgi-zappi-mode-Z{self._zappi_serial_number}-4-0-0-0000"
        self._exec_api_cmd(url)

    def set_zappi_charge_schedule(self, charge_slot_dict_list):
        """@brief Set the charge schedule for the zappi.
           @param charge_slot_dict_list A list of dicts holding the start stop details of the charge."""
        if len(charge_slot_dict_list) > 4:
            raise Exception("Unable to set zappi charge schedule as only 4 schedules can be set.")

        charge_str_list = []
        for charge_slot_dict, slot_id in zip(charge_slot_dict_list, MyEnergi.VALID_ZAPPI_SLOT_ID_LIST):
            charge_str = self._get_zappi_charge_string(charge_slot_dict, slot_id)
            charge_str_list.append(charge_str)

        # I tried removing any existing charge schedules.
        # However if this is executed then the charge schedule fails to get set.
        # Not sure why this occurs.
#        self.set_all_zappi_schedules_off()

        # The zappi charger must be in eco+ mode.
        # We don't need to set eco+ mode as this is checked at a higher level

        # Set each schedule.
        for charge_str in charge_str_list:
            url = MyEnergi.BASE_URL + f"cgi-boost-time-Z{self._zappi_serial_number}-"+charge_str
            self._exec_api_cmd(url)

    def get_zappi_ev_charge_kwh(self):
        """@return Get the EV charge since the car was plugged in."""
        return self._get_zappi_stat('che')