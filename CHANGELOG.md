# CHANGELOG 

## [v1.0.0] (2025-02-19)

### Added
- Initial release.


## [v1.0.1] (2025-03-31)

### Added
- Support ROS2-Iron.

### Changed
- Updated package.xml.
- Changed the data type of intensity: from uint16_t to float.
- Update the build script, added some message displays.
- Used static linking for seyond_sdk.
- Updated log message to display the code location.

### Fixed
- Fixed the issue of the bloom-generate build.
- Fixed the submodule link error.


## [v1.0.2] (2025-08-13)

### Changed
- Updated the submodule seyond_sdk to v3.103.1.
- Deleted the unused cmake config.
- Added the submodule instructions to the README.md.

### Fixed
- Fixed replay_rosbag parameter error.
- Fixed the packet loss rate display error.
- Fixed launch file comment error.


## [v1.0.3x] (unreleased)

### Added
- Support falcon ring_id calculation.

### Changed
- Use first packet timestamp as the frame timestamp.
- Updated Copyright info in source files.
- Updated the submodule seyond_sdk to v3.103.4.
- Synced the LICENSE file with seyond_sdk.

### Fixed
- Updated the package dependencies.


## [v1.0.8-dual] (unreleased)

### Changed
- Updated the submodule seyond_sdk from v3.103.4 to release-3.103.13.

### Fixed (from upstream seyond_sdk 3.103.4 -> 3.103.13)
- Memory corruption in `InnoLidarClient`: `inputParam_` was left uncopied on the
  client-param path; UDP port/IP negotiation reworked with a retry round and
  explicit validation (`get_set_udp_ports_ip`) instead of the old inline item list.
- `stage_client_deliver`: new `disable_do_crc` config key, allowing the per-packet
  CRC to be skipped in the deliver stage.
- `stage_client_read.cpp` substantially reworked (-81 lines) on the read path.
- `robin_nps_adjustment.h` updated (Robin-family NPS adjustment).
- UDP listener now logs the kernel's effective SO_RCVBUF at bind time
  (observability only - it reports the buffer, it does not enlarge it).
- `resource_stats` accounting updated.

### Added (upstream, not used by this fleet)
- Support for hd1-w and E2; HB fault table; HD IMU recording in get_pcd.
