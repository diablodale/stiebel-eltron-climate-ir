# Changelog

Automatically generated from this repository's conventional commits.

## v0.5.0 (2026-08-25)

### Features

- add diagnostics to the system information page
- settle the header mark against the appliance
- let the bench transmit, not only record
- brand logos and icons
- control the appliance's display unit as an entity
- **hardware**: capture real IR frames from inside Home Assistant
- follow the physical remote via an optional infrared receiver (phase 5)
- add the Home Assistant climate integration (phase 4)
- add ACP 35 IR frame encoder and decoder (phase 3)

### Fixes

- remove Acp35Fan.AUTO, no such speed exists
- rotate the journal so one journal file is one run
- apply every frame in a receive buffer, not only the first
- stop replaying a timer heard from the remote
- keep the shadow state in one store, not one copy per entity
- b1 bit 3 is the timer's direction, not that one is set
- follow the physical remote's observed behavior
