# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (APIUM fork)
- Room sync script (`scripts/manage_homekit_rooms.py`) rewritten: standard library only, reads Home Assistant areas over the REST API, moves only accessories behind a Home Assistant bridge by UUID, creates missing rooms, snapshot and restore, `make sync` and `make sync-apply`
- New `create_room` tool; `get_all_accessories`, `get_all_rooms`, `set_accessory_room` and `create_room` return `structuredContent`, and accessories include `bridge_uuid` and `bridge_serial_number`
- HTTP server listens on 127.0.0.1 only, refuses browser requests and other `Host` values, reads whole requests by `Content-Length`, and reports HomeKit loading and permission errors
- Removed the Server-Sent Events endpoint and the welcome page
- CI builds the Mac Catalyst app and runs the Python tests

### Added
- Initial HomeKit MCP Server implementation
- HTTP-based MCP protocol support
- Three core tools: `get_all_accessories`, `get_all_rooms`, `set_accessory_room`
- Mac Catalyst app for HomeKit framework access on macOS
- Comprehensive test suite with CI-friendly Swift Package Manager tests
- SwiftLint integration for code quality
- GitHub Actions CI/CD pipeline
- Full documentation and API reference

### Features
- 🏠 Direct HomeKit integration for accessories and rooms
- 🔧 Move accessories between rooms with UUID-based targeting
- 🌐 RESTful HTTP API with JSON-RPC 2.0 protocol
- 🤖 Claude Code compatible MCP transport
- 🔒 Local-only operation for privacy and security
- 📱 Native macOS app with iOS HomeKit framework

### Technical
- Swift 5.9+ with Mac Catalyst target
- Network framework for HTTP server implementation
- SwiftUI for minimal native interface
- XCTest-based testing with cross-platform support
- SwiftLint for code style enforcement
- Makefile-based build automation

## [1.0.0] - TBD

Initial release targeting full HomeKit MCP functionality.