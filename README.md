# SimpleTP
[English](README.md) | [简体中文](README_zh.md)
A simple teleport plugin aiming to match and extend EssentialsX teleport features.

## Features and Commands
The following examples use the default prefix "!!stp". Adjust according to your configuration file.

### Personal Waypoints
Players can create and manage their own waypoints, which are only visible to themselves.
- **Create Personal Waypoint**: `!!stp setp <name>`
- **Teleport to Personal Waypoint**: `!!stp tpp <name>`
- **Delete Personal Waypoint**: `!!stp delp <name>`
- **List All Personal Waypoints** (clickable for teleport): `!!stp listp`

### Global Waypoints (Public Waypoints)
Global waypoints are visible to all players and suitable for public areas.
- **Create Global Waypoint**: `!!stp setg <name>`
- **Teleport to Global Waypoint**: `!!stp tpg <name>`
- **Delete Global Waypoint**: `!!stp delg <name>`
- **List All Global Waypoints** (clickable for teleport): `!!stp listg`

### Other Commands
- **List All Waypoints** (personal and global): `!!stp list`
- **Return to Last Location**: `!!stp back` (available after teleporting or upon death)

## Configuration File
The configuration file is located at `config/SimpleTP/config.json`
- **prefix**: Command prefix, default is `!!stp`
- **back_on_death**: Whether to automatically record the position upon player death, default is `true`
- **permissions**: Permission configuration

## Dependencies
- **minecraft_data_api**: Used for retrieving player information
- **mg_events**: Used for listening to player death events

## TODO
Sorted by priority:
- [x] Support clickable waypoints
- [x] `back` command supports round-trip teleportation
- [ ] Record player's dimension in waypoints (Nether, Overworld, End)
- [ ] Configuration for cross-dimension teleportation
- [ ] Scheduled saving of waypoint data (to prevent loss on crash)
- [ ] `tp`/`tphere` functionality
- [ ] `tpa`/`tpahere` functionality
- [ ] Add help information
- [ ] Record player's orientation in waypoints
- [ ] Add description information for waypoints
- [ ] Teleport cooldown configuration
- [ ] Maximum number of waypoints configuration
- [ ] Waypoint name length limit configuration
- [ ] Teleport cost configuration (consume custom items or experience) (base cost + distance cost)
- [ ] Multi-language support
- [ ] More feature requests can be submitted in issues🚀