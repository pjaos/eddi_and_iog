# eddi_and_iog

A tool that bridges **Intelligent Octopus Go** and a **myenery EDDI**.

When Octopus Energy schedules an EV charge *outside* the standard off-peak window (23:30–05:30), this tool detects the extra dispatch slot and automatically programmes the myenergi EDDI to heat water (using an emersion heater) from grid power during that period — letting you take advantage of cheap electricity.

---

## Who is this for?

This tool is only useful if you have **all three** of the following:

- An electric vehicle (EV)
- An **Intelligent Octopus Go** tariff (used to charge the EV)
- A **myenergi EDDI unit**

---

## How it works

1. Every 3 minutes (configurable) the tool queries the **Octopus Energy GraphQL API** for planned dispatches.
2. Any dispatch slot whose start or end falls *outside* the standard 23:30–05:30 off-peak window is treated as an extra intelligent slot.
3. If the tool is currently inside such a slot and no myenergi EDDI schedule is active, it updates the schedule into **time slot 4** on the myenergi EDDI (reserved for this purpose, so slots 1, 2 & 3 for your fixed overnight schedule are untouched).
4. When the slot ends, the myenergi EDDI schedule is automatically cleared.

---

## Prerequisites

- Hardware to run the app on. I have tested it on a Raspberry Pi 2 W but it should run on any Linux, windows or MAC machine that meets the python requirements.
- Python **3.11.2** or later
- A **myenergi API Key and EDDI serial number** — You can find details of how to get a myenergi API key at [myenergi API Key](https://support.myenergi.com/hc/en-gb/articles/5069627351185-How-do-I-get-an-API-key)
- An **Octopus Energy API key** — available in your Octopus account dashboard

---

## Installation

The python wheel installer file can be found in the linux folder.

### Using the bundled installer

```bash
python3 install.py eddi_and_iog-<version>-py3-none-any.whl
```

This creates a virtual environment, installs all dependencies, and adds a `eddi_and_iog` launcher to your PATH.

### Manual installation with pip

```bash
pip install eddi_and_iog-<version>-py3-none-any.whl
```

---

## Configuration

All credentials are supplied via a `.env` file. Generate a template with:

```bash
eddi_and_iog -c
```

This creates `/home/auser/eddi_and_iog.env`. Open it and fill in your details:

```env
OCTOPUS_API_KEY=sk_live_XXXXXXXXXXXXXXXXXXXXXXXX
OCTOPUS_ACCOUNT_NO=XXXXXXXXXX
MYENERGI_API_KEY=XXXXXXXXXXXXXXXXXXXXXXXX
MYENERGI_EDDI_SN=XXXXXXXX
MYENERGI_EDDI_TANK=TOP
```

At least one output on your EDDI unit must be connected to an emersion heater on the water tank (TOP and BOTTOM are value MYENERGI_EDDI_TANK values).

### Optional environment variables

| Variable | Default | Description |
|---|---|---|
| `POLL_INTERVAL` | `180` | Polling interval in seconds |

---

## Usage

```bash
eddi_and_iog -e /home/auser/eddi_and_iog.env
```

### Command-line options

| Flag | Description |
|---|---|
| `-e / --env <path>` | Path to the `.env` file (required). This must be an absolute path. |
| `-c / --create_env_file` | Create a template `.env` file in your home directory |
| `-d / --debug` | Enable verbose debug logging |
| `--enable_auto_start` | Register the tool to start on system boot |
| `--disable_auto_start` | Un-register the tool to start on system boot |
| `--check_auto_start` | Check the running status |

### Running as a service

Use the built-in boot manager to have the tool start automatically:

```bash
eddi_and_iog -e /home/auser/eddi_and_iog.env --enable_auto_start
```

---

## Project structure

```
eddi_and_iog/
├── src/
│   └── eddi_and_iog/
│       └── eddi_and_iog.py   # Main application
├── install.py                 # Cross-platform installer
├── pyproject.toml
└── README.md
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `requests` | HTTP calls to Octopus and SolisCloud APIs |
| `python-dotenv` | Loading credentials from the `.env` file |
| `p3lib` | Logging, boot manager, and CLI utilities |

---

## Security notes

- Keep your `.env` file private — it contains API secrets. Do **not** commit it to version control.

---

## Troubleshooting

**"Failed to fetch Octopus dispatches"**
Check your `OCTOPUS_API_KEY` and `OCTOPUS_ACCOUNT_NO` values and ensure you are on the Intelligent Octopus Go tariff.

**"myenergi API error"**
Verify your `MYENERGI_API_KEY`, `MYENERGI_EDDI_SN`.

**EDDI not heating hot water during the slot**
Check that `MYENERGI_EDDI_TANK` is details the EDDI output connected to an emersion heater on the hot water tank.

---

## Licence

MIT — see [LICENSE](LICENSE.txt) for details.

---

## Author

Paul Austen — [pjaos@gmail.com](mailto:pjaos@gmail.com)


## Acknowledgements

Development of this project was assisted by [Claude](https://claude.ai) (Anthropic's AI assistant),
which contributed to code review, bug identification, test generation, and this documentation.