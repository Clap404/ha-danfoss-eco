# Danfoss Eco (eTRV) for Home Assistant

![Danfoss Eco](custom_components/danfoss_eco/brand/icon%402x.png)

Home Assistant custom integration for **Danfoss Eco** Bluetooth Thermostatic Radiator Valves (eTRV). Local-only, talks directly to the TRV over BLE. This integration aims to transform your TRVs from a programmable device into a connected one.

## Features

Exposes connected TRV's as home-assistant thermostats. Devices can be paired directly from the integration ui. The integration polls valves every hour to get updates on ambient temperatures and manual temperature set point changes.

Features :

- Heat, Auto (schedule) and Off (frost protection) modes
- Pin (untested)
- Child lock
- Vacation and Pause presets
- Battery level reporting
- Ambient temperature reporting
- On-device weekly schedules (read + write, see [Setting a schedule](#setting-a-schedule))

## Requirements

- Home Assistant `2024.1.0`+
- A working Bluetooth adapter known to Home Assistant (ESPHome BT proxy works too).
- The TRV's 4-digit PIN (default `0000`).

## Install

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories → add `https://github.com/Clap404/ha-danfoss-eco` as type _Integration_.
2. Install **Danfoss Eco (eTRV)**, restart Home Assistant.

### Manual

Copy `custom_components/danfoss_eco/` into your HA `config/custom_components/` directory, restart.

## Pairing a device

1. Settings → Devices & Services → **Add Integration** → _Danfoss Eco_. Discovered TRVs appear automatically; otherwise pick from the list.
2. Enter the 4-digit PIN (`0000` if never changed).
3. When prompted, **short-press the button on the TRV** — its LED goes solid. The integration reads the device's secret key during that window.
4. If pairing times out, press the button again and retry. The window is only a few seconds.

## Setting a schedule

The TRV runs a weekly heating program on-device: it holds a **home** temperature during comfort periods and falls back to an **away** temperature the rest of the time. Set it with the `danfoss_eco.set_schedule` service.

Go to **Settings → Devices & Services → Danfoss Eco → Configure**. Set the **home** and **away** temperatures, then type each day's comfort periods into its box:

- Morning + evening block: `06:00-08:30, 16:30-22:30`
- Up to 3 periods per day: `07:30-10:00, 12:00-14:00, 17:00-23:00`
- Warm all day: `07:30-23:00`
- Leave a day empty for away temperature all day.

Times must land on a **30-minute grid** (`06:00`, `06:30`, …); use `00:00` as an end time to mean end-of-day. Fields are prefilled with the schedule currently on the device.

The schedule only drives temperature while the thermostat is in **Auto** (schedule) mode. In **Heat** (manual) mode the TRV holds your set point and ignores the program.

> The same schedule can also be set from automations/scripts via the `danfoss_eco.set_schedule` action, using the same `"start-end"` text per day.

## Modes and presets

The device has one operating mode; the integration maps it to Home Assistant concepts:

| HVAC mode | Device mode | Behavior |
|---|---|---|
| **Heat** | Manual | The TRV holds your target set point. |
| **Auto** | Schedule | The TRV follows its on-device weekly schedule. The displayed target temperature is derived from that schedule, so it follows the comfort/away transitions without polling the (sleepy, battery-powered) device. |
| **Off** | Manual | Not a real device mode: the integration sets Manual mode and writes the **frost protection temperature** as the set point. |

Two overlays sit on top of the base mode, exposed as climate presets:

| Preset | Device mode | Behavior |
|---|---|---|
| *(none)* | Manual or Schedule | Normal operation. |
| **Vacation** | Vacation | The TRV actively holds the **vacation temperature** (a number entity on the device page). Switching back to preset *none* restores the mode you were in. |
| **Pause** | Pause | The TRV stops regulating and leaves the valve at its current position — useful for radiator maintenance. While paused, the climate card still shows the underlying mode and last set point; nothing tracks a temperature. Preset *none* resumes the recorded mode. |

The device stores the mode to return to after vacation/pause, so the overlays compose cleanly with both Heat and Auto.

## Notes

- Scan interval is set to one hour because these TRV were not originally made for frequent communication. Increasing poll rate may drain the battery faster. Using the default, you should at least a year of battery life.

## Credits

This project has been largely AI generated. As such, I consider it a derivative work : protocol decoding, crypto handling, and characteristic mapping were lifted and ported from:

- [AdamStrojek/libetrv](https://github.com/AdamStrojek/libetrv.git) (Apache 2.0)
- [keton/etrv2mqtt](https://github.com/keton/etrv2mqtt) (MIT)
- [dmitry-cherkas/esphome-danfoss-eco](https://github.com/dmitry-cherkas/esphome-danfoss-eco) (MIT)

Huge thanks to the original authors for reverse engineering and publishing their projects many years ago.

## License

MIT — see [`LICENSE`](LICENSE.md). Compatible with upstream MIT licensing of the projects above.
