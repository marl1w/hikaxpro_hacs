# hikaxpro_hacs
HACS repository of Hikvision Ax Pro integration for home assistant

**Type**: Local integration (not using any cloud connection - only connecting to device)
**IOT Class**: `local_polling` aka Pulling data from device in predefined interval (default 30 sec)

> **Fork note**: this fork adds native **zone bypass management** (bypass services,
> per-zone "bypassable" configuration and an opt-in auto-bypass-on-arming logic).
> See [Zone bypass & auto-bypass on arming](#zone-bypass--auto-bypass-on-arming).
> With the auto-bypass option disabled (the default) the integration behaves
> exactly like upstream, except that a refused arm command now raises a visible
> error instead of being silently ignored.

For reporting issue with log please use issues.
For feature request there is a feature request in issues.
For other questions checkout first [#FAQ](#faq) section. Otherwise, use [discussions](https://github.com/petrleocompel/hikaxpro_hacs/discussions).

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

## Supported devices
- AX Pro series
- AX Hub - introduced in version 1.2.0 

## Support
- Sub zones control (Opt-in - after configuration reload integration)

### Supported Sensors / Detectors
- Wireless external magnetic sensors
- Wireless slim magnetic sensor
- Wireless magnet Shock Detector
- Wireless temperature humidity sensors
- Wireless PIR sensors
- Wireless glass break sensors
- Wireless PIR AM curtain sensor
- Wireless PIR CAM sensor
- Wired magnetic contact sensor
- Wireless Smoke Detector

### Attributes
- Alarm
- Armed
- Battery
- Bypass
- Humidity
- Is via repeater
- Magnet presence
- Signal
- Stay away
- Status
- Tamper
- Temperature

### Examples
Example screens of integration. 

**Magnetic Sensor**
![Magnetic Sensor](https://user-images.githubusercontent.com/9423543/222737996-4eefb9a5-a09a-4713-a87e-71664580aaf2.png)

**PIR Sensor**
![PIR Sensor](https://user-images.githubusercontent.com/9423543/222738007-1961348c-9e94-46de-9a29-40aedc726e38.png)

**Main System Device**
![Main System Device](https://user-images.githubusercontent.com/9423543/224548626-823a6cfa-5c15-4a6a-97d2-32831797253c.png)


## Installation

### Pre-check
> ⚠️ Please make sure your user you will be using will have role "Admin" and it is not same as "Installer" or remove the "Installer" completely. (Referring to issue #108)


#### Firmware
⚠️ Last working stable FW - `1.2.9 Build: 240621`.
Warning for now. Newer firmware was reported that causes instabilities. 


### HACS

1. Install HACS if you don't have it already
2. Open HACS in Home Assistant
3. Go to "Integrations" section
4. Click ... button on top right and in menu select "Custom repositories"
5. Add repository https://github.com/xxxx/hikaxpro_hacs and select category "Integration"
6. Search for "hikaxpro_hacs" and install it
7. Restart Home Assistant

### Manual

Download the zip and extract it. Copy the folder `hikaxpro_hacs` to your `custom_components` folder.

## Zone bypass & auto-bypass on arming

This fork adds full zone-bypass management on top of upstream, driven through the panel's local ISAPI (`/ISAPI/SecurityCP/control/bypass`). Everything is **opt-in and off by default**.

### What you get

- **Services** (usable from scripts/automations, independent of the automatic logic):
  - `hikvision_axpro.bypass_zone` / `hikvision_axpro.unbypass_zone` — target the zone's *Bypass* binary sensor. Every command verifies its outcome against the panel and raises an explicit error on failure.
  - `hikvision_axpro.clear_all_bypasses` — target an alarm panel entity; on an area panel it clears only that area's zones.
- **Per-zone "Bypassable on arming" switch** (config category, default **off**, stored on the HA side and persistent across restarts). Only created for **Instant** zones — the only zone type the panel itself offers the *forbid bypass on arming* option for. Every other zone type (delay, perimeter, follow, 24h, fire, gas, medical, emergency, key) is never auto-bypassed and gets no switch. For instant zones with the panel-side **"forbid bypass on arming"** setting enabled (`armNoBypassEnabled`, *Vieta esclusione su inserimento*) the switch is forced **off and not editable** (shown unavailable): the panel configuration wins over the HA flag, so a fault on such a zone always blocks arming. The zone configuration is refreshed at startup and then hourly, so a change made in the Hikvision app is picked up within an hour (or immediately on reload).
- **Per-panel options**:
  - *Arming modes with automatic bypass of faulted zones* (multi-select: *Home*, *Away*, *Vacation*; default none — aligned with the arm actions visible in Home Assistant).
  - *Re-enable debounce* (default 10 s).
  - *On disarm, remove all bypasses* (default off — normally only integration-applied bypasses are cleaned up).
- **Ready-to-arm binary sensors** (one per arming mode: home, away, vacation — always created, independent of the auto-bypass configuration, as building blocks for automations): **on** when arming in that mode would succeed — no zone in fault, or every faulted zone is marked bypassable. The home-mode sensor ignores zones the panel itself bypasses on stay arming (`stayAway`). Attributes expose `blocking_zones`, `zones_to_bypass` and a per-area breakdown. They are computed from polled data, so they are advisory: the authoritative check runs synchronously at arm time.
- **Vacation arming**: the panels expose `alarm_arm_vacation` in addition to home/away.
- **Events** on the HA bus for automations/notifications:
  `hikvision_axpro_bypass_applied`, `hikvision_axpro_bypass_removed` (reason:
  `health_recovered` / `disarm` / `rollback` / `service` / `reconciled`),
  `hikvision_axpro_arming_blocked`.

### How arming works with auto-bypass enabled

When you arm through HA in a mode enabled for auto-bypass (home/away/vacation),
before any arm command is sent the integration:

1. Reads **fresh** zone states from the panel (never stale polled data; a read failure aborts arming).
2. A zone is **in fault** when it is open/triggered, offline or tampered. Low battery is a warning only: it neither blocks arming nor causes a bypass. When arming home, zones the panel stay-bypasses itself are ignored.
3. Every faulted zone marked *bypassable* is bypassed, with verification.
4. If any faulted zone is **not** bypassable, or any bypass fails, arming is **aborted**: nothing is sent to the panel, already-applied bypasses are rolled back, and an error listing the blocking zones is raised. There is no permissive mode.
5. Only then the arm command is sent.

While armed, a zone bypassed by the integration that returns healthy and stays so for the debounce period is **automatically re-enabled**: close the door that was open at arm time and, after the debounce, the zone is armed again — a new opening triggers an alarm. The bypass is never removed while the zone is still in fault. Every step of this pipeline is logged at INFO/WARNING level (`recovered while armed`, `re-enabled after recovery`, `Panel refused to unbypass`), so a re-enable that does not happen can be diagnosed from the HA log. On disarm (from any source, including keypad and Hik-Connect) all integration-applied bypasses are removed.

The integration only manages bypasses **it applied itself** (via the arming logic or its services). Bypasses applied via keypad, Hik-Connect or the web interface are shown but never touched (unless you enable *remove all bypasses on disarm*), and arming performed outside HA never triggers auto-bypass.

### Security implications — read before enabling

- Marking a zone *bypassable* means an arming command can silently disable it while it is in fault. Only flag zones whose entry is protected by a second, non-bypassable sensor.
- **Dual-technology entry (recommended setup)**: front door with a magnetic contact + a PIR/mmWave curtain detector. Mark the **magnetic contact** as bypassable (a door left open should not stop you from arming) and keep the **curtain detector non-bypassable**. If both are in fault, arming is blocked and the entry is never silently left unprotected.
- The pre-arm bypass removes faulted zones from the panel's own arming fault check, so the panel-side **"Arm with Fault" option can and should remain disabled**. Verify on your firmware that bypassed zones are indeed excluded from the arming fault check; if the panel still refuses to arm, the refusal is surfaced as an HA error.

## FAQ

### Can I see sensors with this?
Yes, supported sensors are [listed above](#supported-sensors--detectors).
> ⚠️ But there is a delay of pulling interval to see the data / the status. 

### Can I make data appear faster? / Can I speed up updating sensors?
Yes and No. **Default is 30 seconds**.

Yes you can lower the `pull interval` via configuration of integration.
> ⚠️ But you can hit a limit with number of devices and cameras. Your system can become unstable.
> But I admit some smaller system can run with 2 seconds `pull interval` stable. It also depends on your device. 

Examples:
- [Real time update and sensor excluding #157](https://github.com/petrleocompel/hikaxpro_hacs/issues/157)
- [Hikvision IP Cam disconnected by AX Pro #124](https://github.com/petrleocompel/hikaxpro_hacs/issues/124)

### Is there another way to do it? Receive events only?
**No**. HikVision does not have that API. Maybe there could be, but I did not find it.
Documentation of HikVision devices is now behind "partner portal".
You have to be in partner program and login to get current documentation.  
I am using old documentation and even there a some API is not documented. Or statuses and attributes.

I still have some stuff to implement to improve the situation ([found event stream for arm/disarm/alarm #143](https://github.com/petrleocompel/hikaxpro_hacs/issues/143#issuecomment-2539032085)) but this is the current state.

### Why do not get in touch with HikVision to improve?
**Maybe they would have a problem with this integration**. I really do not want to get **DMCA** request.
Examples:
- https://www.home-assistant.io/blog/2023/10/13/removal-of-mazda-connected-services-integration/
- https://github.com/Andre0512/hOn?tab=readme-ov-file#takedown-story

### Cannot arm my system with HA.

Check your batteries for devices. Check that all zones are closed / not triggered.

If you use Hik-Connect app - you can press the **diagnosis** button on the "system overview page".
It will tell you why you cannot arm the system. 

This fork can bypass zones: see [Zone bypass & auto-bypass on arming](#zone-bypass--auto-bypass-on-arming).
Upstream does not bypass any "zones" so there you might have to set it up via "Hik-Connect" / Web interface.

### Everything seems fine I can arm in "Hik-Connect" app but not in HA.

Is installer account in the system ? What account you are using with the integration?
Sadly to make it work we need to be an Admin user.

Some systems work with login "admin" some with the user who set it up via Hik-Connect account.
So even your "Hik-Connect" used email address might be the correct one.

But still in "Web Interface" of your device after you log in there should not be and Installer account. 
If there is and if it has a same email as your user it is the problem and needs to be removed.
