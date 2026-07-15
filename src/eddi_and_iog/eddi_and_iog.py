"""
eddi_and_iog.py
================================
Monitors Octopus Energy Intelligent Go for extra (outside 23:30-05:30) dispatch
slots and mirrors them as charge-time windows on a Myenergi eddi unit to heat
hot water from grid power.

How it works:
    1. Every POLL_INTERVAL seconds the Octopus GraphQL API is queried for
       plannedDispatches.
    2. Any dispatch slot whose start OR end falls outside the standard off-peak
       window (23:30-05:30) is considered an "intelligent" extra slot.
    3. If we are currently inside such a slot and eddi schedule is active,
       one is created (charge time slot 3, which we reserve for this purpose).
    4. When the slot ends the schedule is cleared.

eddi API notes:
    - You need a myenergi API Key.
      https://support.myenergi.com/hc/en-gb/articles/5069627351185-How-do-I-get-an-API-key
      details how to get this API key.
    - Myenergy eddi units have 4 schedule time slots. This script uses the last slot by
      default so as not to conflict with your fixed overnight schedule in slots 1, 2 & 3.
    - The Myenergy eddi can control 2 heaters TOP and BOTTOM. The default is TOP.
"""

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from p3lib.uio import UIO
from p3lib.helper import logTraceBack
from p3lib.boot_manager import BootManager
from p3lib.helper import get_program_version

from dotenv import load_dotenv
from eddi_and_iog.octopus import OctopusClient
from eddi_and_iog.myenergi import MyEnergi

# ---------------------------------------------------------------------------
# EddiSyncApp
# ---------------------------------------------------------------------------
class EddiSyncApp:
    """
    Orchestrates the sync loop between OctopusClient and MyEnergi.
    Polls Octopus for extra dispatch slots and keeps the MyEnergi eddi
    charge schedule in sync.
    """

    @staticmethod
    def fmt_time(dt: datetime | None) -> str:
        """Format a datetime as HH:MM in local time for the MyEnergi API."""
        return dt.astimezone().strftime("%H:%M") if dt is not None else "??:??"

    def __init__(self,
                 octopus: OctopusClient,
                 myenergy: MyEnergi,
                 poll_interval: int = 180,
                 uio = None):
        self.octopus       = octopus
        self.myenergy      = myenergy
        self.poll_interval = poll_interval
        self._uio          = uio
        self._slot_active  = False
        self._tank_str = os.getenv("MYENERGI_EDDI_TANK",  "")
        self._tank = MyEnergi.get_tank_id(self._tank_str)
        self._active_start: datetime | None = None
        self._active_end:   datetime | None = None

        # Limit the Octopus API usage
        if self.poll_interval < 60:
            self.poll_interval = 60

    def _info(self, msg):
        if self._uio:
            self._uio.info(f"Octopus API: {msg}")

    def _debug(self, msg):
        if self._uio:
            self._uio.debug(f"Octopus API: {msg}")

    def run(self) -> None:
        """Start the polling loop. Runs indefinitely."""
        self._info("=== Octopus -> eddi Intelligent water heating starting ===")
        self._info(f"Account: {self.octopus.account_number} | eddi: {self.myenergy.get_eddi_serial_number()} | TANK: {self._tank_str} | Poll: {self.poll_interval}s")
        # Clear any reserved boost slot left over from a previous run. myenergi
        # boost schedules recur weekly, so a slot left set by a crashed run
        # would otherwise re-fire on the same weekday next week. This only
        # touches our reserved slot, leaving the fixed overnight schedules alone.
        try:
            self.myenergy.set_tank_schedule(False, None, None, self._tank)
            self._info("Cleared reserved eddi boost slot at startup.")
        except Exception as exc:
            self._info(f"Could not clear reserved eddi boost slot at startup: {exc}")
        while True:
            self._poll()
            time.sleep(self.poll_interval)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        """Single poll iteration."""
        # Invalidate the cached eddi stats so _log_heater_power() always
        # reads live values rather than values from the previous poll.
        self.myenergy.invalidate_stats_cache()
        dispatch = self.octopus.find_relevant_extra_dispatch()

        if dispatch:
            self._handle_active_dispatch(dispatch)
        else:
            self._handle_no_dispatch()

    def _log_heater_power(self) -> None:
        """Query and log the current CT clamp power readings from the eddi."""
        try:
            on_heater = self.myenergy.get_eddi_heater_number()
            heater_name = MyEnergi.get_tank_name(on_heater)
            on_heater_str = f"{on_heater}" if on_heater is not None else "unavailable"

            on_heater_watts = self.myenergy.get_eddi_selected_heater_watts()
            p1_str = f"{on_heater_watts} W" if on_heater_watts is not None else "unavailable"

            if on_heater_watts:
                self._info(f"{heater_name} heater ({on_heater_str}) is ON and it is drawing {p1_str}")
            else:
                self._info(f"{heater_name} heater ({on_heater_str}) is OFF.")

            tt = self.myenergy.get_eddi_top_tank_temp()
            bt = self.myenergy.get_eddi_bottom_tank_temp()
            tt_str = f"{tt} °C" if tt is not None else "unavailable"
            bt_str = f"{bt} °C" if bt is not None else "unavailable"
            self._info(f"Tank Temp: TOP: {tt_str}, BOTTOM: {bt_str}")

        except Exception as exc:
            self._info(f"Could not read heater power: {exc}")

    def _handle_active_dispatch(self, dispatch: dict) -> None:
        end   = dispatch["end"]
        start = dispatch["start"]

        if not self._slot_active:
            self._info(f"Extra dispatch detected: {EddiSyncApp.fmt_time(start)} -> {EddiSyncApp.fmt_time(end)}")
            try:
                self.myenergy.set_tank_schedule(True, start, end - start, self._tank)
                # Only mark the slot as active once the API call has succeeded.
                self._slot_active  = True
                self._active_start = start
                self._active_end   = end
            except Exception as exc:
                self._info(f"Failed to set eddi schedule: {exc} — will retry next poll.")

        elif self._active_end != end or (self._active_start is not None and self._active_start != start):
            # Octopus has rescheduled the slot (start, end, or both changed).
            self._info(
                f"Dispatch slot changed: {EddiSyncApp.fmt_time(self._active_start)} -> "
                f"{EddiSyncApp.fmt_time(self._active_end)}  =>  "
                f"{EddiSyncApp.fmt_time(start)} -> {EddiSyncApp.fmt_time(end)}, updating eddi."
            )
            try:
                self.myenergy.set_tank_schedule(True, start, end - start, self._tank)
                # Only update the cached times once the API call has succeeded.
                self._active_start = start
                self._active_end   = end
            except Exception as exc:
                self._info(f"Failed to update eddi schedule: {exc} — will retry next poll.")

        else:
            # No change (or _active_start not yet recorded — adopt it silently).
            self._active_start = start
            self._info(f"Dispatch still active: {EddiSyncApp.fmt_time(start)} -> {EddiSyncApp.fmt_time(end)}.")

        self._log_heater_power()

    def _handle_no_dispatch(self) -> None:
        if self._slot_active:
            self._info("No active extra dispatch — clearing eddi charge slot.")
            self.myenergy.set_tank_schedule(False,
                                            None,
                                            None,
                                            self._tank)
            self._slot_active  = False
            self._active_start = None
            self._active_end   = None

        else:
            self._info("No extra dispatch. Sleeping.")

    @staticmethod
    def create_template_env_file(uio):
        home = Path.home()
        template_env_file = os.path.join(home, 'eddi_and_iog.env')
        if os.path.isfile(template_env_file):
            raise Exception(f'{template_env_file} file already present.')

        lines = []
        lines.append(f'OCTOPUS_API_KEY=sk_live_XXXXXXXXXXXXXXXXXXXXXXXX{os.linesep}')
        lines.append(f'OCTOPUS_ACCOUNT_NO=XXXXXXXXXX{os.linesep}')
        lines.append(f'MYENERGI_API_KEY=XXXXXXXXXXXXXXXXXXXXXXXX{os.linesep}')
        lines.append(f'MYENERGI_EDDI_SN=XXXXXXXX{os.linesep}')
        lines.append(f'MYENERGI_EDDI_TANK=TOP{os.linesep}')

        with open(template_env_file, 'w') as fd:
            fd.writelines(lines)

        uio.info(f"Created {template_env_file}")
        uio.info("You should now edit this file to add your Octopus Energy and myenergi details.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """@brief Program entry point"""
    uio = UIO()
    options = None
    try:
        parser = argparse.ArgumentParser(description="A tool to check if your Intelligent Octopus Go account has scheduled an EV charge outside the 23:30-5:30 slot. If this is found to be the case configure your myenergi eddi unit to heat water using grid power in the scheduled period. This allows use the water to be heated during this time on low cost electricity. !!! This tool is only useful to you if you have an EV, are on the Intelligent Octopus Go tariff (used to charge the EV) and have a myenergi eddi unit connected to an emersion heater on your hot water tank.",
                                         formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("-d", "--debug",  action='store_true', help="Enable debugging.")
        parser.add_argument("-e", "--env",    help="The absolute path to the env file containing the Octopus and myenergy details.", default=None)
        parser.add_argument("-c", "--create_env_file",  action='store_true', help="Create a template env file. Once created you must manually update this with the Octopus and myenergi details.")
        BootManager.AddCmdArgs(parser)

        options = parser.parse_args()

        uio.enableDebug(options.debug)
        uio.logAll(True)
        uio.enableSyslog(True, programName="eddi_and_iog")

        prog_version = get_program_version('eddi_and_iog')
        uio.info(f"eddi_and_iog: V{prog_version}")

        handled = BootManager.HandleOptions(uio, options, True)
        if not handled:

            if options.create_env_file:
                EddiSyncApp.create_template_env_file(uio)

            else:
                if not options.env:
                    raise Exception("-e/--env command line argument missing.")

                load_dotenv(dotenv_path=options.env)

                octopus = OctopusClient(
                    api_key        = os.getenv("OCTOPUS_API_KEY",    "sk_live_XXXXXXXXXXXXXXXX"),
                    account_number = os.getenv("OCTOPUS_ACCOUNT_NO", "A-XXXXXXXX"),
                    uio            = uio
                )

                myenergi = MyEnergi(
                    os.getenv("MYENERGI_API_KEY",  ""),
                    uio = uio,
                )
                myenergi.set_eddi_serial_number(os.getenv("MYENERGI_EDDI_SN",  ""))

                app = EddiSyncApp(
                    octopus,
                    myenergi,
                    poll_interval = int(os.getenv("POLL_INTERVAL", "180")),
                    uio = uio,
                )

                app.run()

    # If the program throws a system exit exception
    except SystemExit:
        pass
    # Don't print error information if CTRL C pressed
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        logTraceBack(uio)

        if options and options.debug:
            raise
        else:
            uio.error(str(ex))
            # We delay for 5 seconds here as if this cmd is launched by systemd it will
            # be called again very shortly.
            # If the myenergi API returns an error we don't want to try again to quickly
            # or the myenergi API will start rejecting requests because they are being
            # sent to frequently.
            uio.info("Waiting 5 seconds before exit to reduce myenergi API load.")
            time.sleep(5)

if __name__ == "__main__":
    main()
