import Foundation
import HomeKit

enum ToolResponse {
    static func error(_ request: MCPRequest, code: Int, _ message: String) -> MCPResponse {
        print("❌ [MCP] \(message)")
        return MCPResponse(jsonrpc: "2.0", id: request.id, result: nil, error: MCPError(code: code, message: message))
    }

    static func text(_ request: MCPRequest, _ text: String, meta: [String: Any]? = nil) -> MCPResponse {
        var result = ["content": AnyEncodable([["type": "text", "text": text]])]
        if let meta = meta {
            result["_meta"] = AnyEncodable(meta)
        }
        return MCPResponse(jsonrpc: "2.0", id: request.id, result: result, error: nil)
    }

    static func structured(_ request: MCPRequest, text: String, _ content: [String: Any]) -> MCPResponse {
        MCPResponse(jsonrpc: "2.0", id: request.id,
                    result: [
                        "content": AnyEncodable([["type": "text", "text": text]]),
                        "structuredContent": AnyEncodable(content)
                    ], error: nil)
    }
}

final class HomeKitTools: NSObject, HMHomeManagerDelegate {
    typealias Reply = (MCPResponse) -> Void

    static let notReadyCode = -32000
    static let notAuthorisedCode = -32001
    private static let requestTimeout: TimeInterval = 20
    private static let characteristicReadTimeout: TimeInterval = 5
    private static let homeAssistantBridgeSerial = "homekit.bridge"

    private let homeManager = HMHomeManager()
    private var homesLoaded = false

    override init() {
        super.init()
        homeManager.delegate = self
    }

    func homeManagerDidUpdateHomes(_ manager: HMHomeManager) {
        print("🏠 [HomeKit] Homes updated. Found \(manager.homes.count) homes:")
        for home in manager.homes {
            print("   - \(home.name): \(home.accessories.count) accessories, \(home.rooms.count) rooms")
        }
        homesLoaded = true
    }

    func call(_ request: MCPRequest, reply: @escaping Reply) {
        let once = SingleUseCallback<MCPResponse>(reply)
        guard let toolName = request.params?["name"]?.value as? String,
              let arguments = (request.params?["arguments"]?.value ?? [String: Any]()) as? [String: Any] else {
            once(ToolResponse.error(request, code: -32602, "Invalid params"))
            return
        }
        guard ToolCatalog.definition(named: toolName) != nil else {
            once(ToolResponse.error(request, code: -32601, "Tool not found: \(toolName)"))
            return
        }
        if let unavailable = unavailableReason() {
            once(ToolResponse.error(request, code: unavailable.code, unavailable.message))
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.requestTimeout) {
            once(ToolResponse.error(request, code: -32603, "Timed out waiting for HomeKit"))
        }
        do {
            try dispatch(toolName, request, arguments) { once($0) }
        } catch {
            once(ToolResponse.error(request, code: -32602, error.localizedDescription))
        }
    }

    private func unavailableReason() -> (code: Int, message: String)? {
        let status = homeManager.authorizationStatus
        if status.contains(.determined) && !status.contains(.authorized) {
            return (Self.notAuthorisedCode, "HomeKit access is not authorised for HomeKitMCP; "
                    + "allow it in System Settings > Privacy & Security > HomeKit")
        }
        guard homesLoaded else {
            return (Self.notReadyCode, "HomeKit has not finished loading homes; retry in a few seconds")
        }
        return nil
    }

    private func dispatch(_ toolName: String, _ request: MCPRequest, _ arguments: [String: Any],
                          reply: @escaping Reply) throws {
        switch toolName {
        case "get_all_accessories":
            getAllAccessories(request, reply: reply)
        case "get_all_rooms":
            reply(getAllRooms(request))
        case "set_accessory_room":
            try setAccessoryRoom(request, SetAccessoryRoomArguments(arguments), reply: reply)
        case "create_room":
            try createRoom(request, CreateRoomArguments(arguments), reply: reply)
        case "get_accessory_by_name":
            reply(getAccessoryByName(request, try ToolArguments.string(arguments, "name")))
        case "get_room_by_name":
            reply(getRoomByName(request, try ToolArguments.string(arguments, "name")))
        case "set_accessory_room_by_name":
            try setAccessoryRoomByName(request, arguments, reply: reply)
        case "rename_accessory":
            try renameAccessory(request, arguments, reply: reply)
        case "rename_room":
            try renameRoom(request, arguments, reply: reply)
        case "get_room_accessories":
            reply(getRoomAccessories(request, try ToolArguments.string(arguments, "room_name")))
        case "accessory_on":
            control(request, try ToolArguments.string(arguments, "accessory_name"), action: .turnOn, reply: reply)
        case "accessory_off":
            control(request, try ToolArguments.string(arguments, "accessory_name"), action: .turnOff, reply: reply)
        case "accessory_toggle":
            control(request, try ToolArguments.string(arguments, "accessory_name"), action: .toggle, reply: reply)
        default:
            reply(ToolResponse.error(request, code: -32601, "Tool not found: \(toolName)"))
        }
    }

    private static func finish(_ error: Error?, _ request: MCPRequest, reply: @escaping Reply, failure: String,
                               success: @escaping () -> MCPResponse) {
        DispatchQueue.main.async {
            if let error = error {
                reply(ToolResponse.error(request, code: -32603, "\(failure): \(error.localizedDescription)"))
            } else {
                reply(success())
            }
        }
    }

    private static func jsonValue(_ value: String?) -> Any {
        if let value = value {
            return value
        }
        return NSNull()
    }

    private static func informationCharacteristic(_ accessory: HMAccessory, _ type: String) -> HMCharacteristic? {
        accessory.services
            .first { $0.serviceType == HMServiceTypeAccessoryInformation }?
            .characteristics
            .first { $0.characteristicType == type }
    }

    private static func informationValue(_ accessory: HMAccessory, _ type: String) -> String? {
        informationCharacteristic(accessory, type)?.value as? String
    }

    private func homeRooms(_ home: HMHome) -> [HMRoom] {
        let defaultRoom = home.roomForEntireHome()
        return [defaultRoom] + home.rooms.filter { $0.uniqueIdentifier != defaultRoom.uniqueIdentifier }
    }

    private static func roomInfo(_ room: HMRoom, in home: HMHome) -> [String: Any] {
        [
            "uuid": room.uniqueIdentifier.uuidString,
            "name": room.name,
            "home": home.name,
            "home_uuid": home.uniqueIdentifier.uuidString
        ]
    }

    private func findAccessory(named name: String) -> (accessory: HMAccessory, home: HMHome)? {
        let needle = name.lowercased()
        for home in homeManager.homes {
            if let accessory = home.accessories.first(where: { $0.name.lowercased().contains(needle) }) {
                return (accessory: accessory, home: home)
            }
        }
        return nil
    }

    private func findRoom(named name: String) -> (room: HMRoom, home: HMHome)? {
        let needle = name.lowercased()
        for home in homeManager.homes {
            if let room = home.rooms.first(where: { $0.name.lowercased().contains(needle) }) {
                return (room: room, home: home)
            }
        }
        return nil
    }

    private func readValues(_ characteristics: [HMCharacteristic], completion: @escaping () -> Void) {
        guard !characteristics.isEmpty else {
            completion()
            return
        }
        let done = SingleUseCallback<Void> { _ in completion() }
        let group = DispatchGroup()
        for characteristic in characteristics {
            group.enter()
            characteristic.readValue { _ in
                group.leave()
            }
        }
        group.notify(queue: .main) {
            done(())
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.characteristicReadTimeout) {
            done(())
        }
    }

    private func getAllAccessories(_ request: MCPRequest, reply: @escaping Reply) {
        let unreadSerials = homeManager.homes
            .flatMap { $0.accessories }
            .compactMap { Self.informationCharacteristic($0, HMCharacteristicTypeSerialNumber) }
            .filter { $0.value == nil }
        readValues(unreadSerials) { [weak self] in
            guard let self = self else {
                return
            }
            reply(self.allAccessoriesResponse(request))
        }
    }

    private func allAccessoriesResponse(_ request: MCPRequest) -> MCPResponse {
        var accessories: [[String: Any]] = []
        var lines: [String] = []

        for home in homeManager.homes {
            let defaultRoom = home.roomForEntireHome()
            var bridges: [UUID: HMAccessory] = [:]
            for bridge in home.accessories {
                for bridged in bridge.uniqueIdentifiersForBridgedAccessories ?? [] {
                    bridges[bridged] = bridge
                }
            }
            let haBridges = home.accessories.filter {
                Self.informationValue($0, HMCharacteristicTypeSerialNumber) == Self.homeAssistantBridgeSerial
            }
            let fallbackBridge = haBridges.count == 1 ? haBridges.first : nil
            for accessory in home.accessories {
                let room = accessory.room ?? defaultRoom
                let category = accessory.category.localizedDescription
                let serial = Self.informationValue(accessory, HMCharacteristicTypeSerialNumber)
                let bridge = bridges[accessory.uniqueIdentifier] ?? (accessory.isBridged ? fallbackBridge : nil)
                let bridgeSerial = bridge.flatMap { Self.informationValue($0, HMCharacteristicTypeSerialNumber) }
                accessories.append([
                    "uuid": accessory.uniqueIdentifier.uuidString,
                    "name": accessory.name,
                    "serial_number": Self.jsonValue(serial),
                    "room": room.name,
                    "room_uuid": room.uniqueIdentifier.uuidString,
                    "home": home.name,
                    "home_uuid": home.uniqueIdentifier.uuidString,
                    "category": category,
                    "reachable": accessory.isReachable,
                    "firmware": Self.jsonValue(Self.informationValue(accessory, HMCharacteristicTypeFirmwareVersion)),
                    "bridge_uuid": Self.jsonValue(bridge?.uniqueIdentifier.uuidString),
                    "bridge_serial_number": Self.jsonValue(bridgeSerial)
                ])
                lines.append("• \(accessory.name) (\(category)) - Room: \(room.name), "
                             + "UUID: \(accessory.uniqueIdentifier.uuidString), S/N: \(serial ?? "unknown")")
            }
        }

        let text = "Found \(accessories.count) accessories:\n" + lines.joined(separator: "\n")
        return ToolResponse.structured(request, text: text, ["accessories": accessories])
    }

    private func getAllRooms(_ request: MCPRequest) -> MCPResponse {
        var rooms: [[String: Any]] = []
        var lines: [String] = []

        for home in homeManager.homes {
            let defaultRoomUUID = home.roomForEntireHome().uniqueIdentifier
            for room in homeRooms(home) {
                let isDefault = room.uniqueIdentifier == defaultRoomUUID
                var info = Self.roomInfo(room, in: home)
                info["is_default"] = isDefault
                rooms.append(info)
                let marker = isDefault ? " [default]" : ""
                lines.append("• \(room.name)\(marker) in \(home.name) (UUID: \(room.uniqueIdentifier.uuidString))")
            }
        }

        return ToolResponse.structured(request, text: "Found \(rooms.count) rooms:\n" + lines.joined(separator: "\n"),
                                       ["rooms": rooms])
    }

    private func setAccessoryRoom(_ request: MCPRequest, _ args: SetAccessoryRoomArguments,
                                  reply: @escaping Reply) {
        let accessoryUUID = args.accessoryUUID
        guard let home = homeManager.homes.first(where: { candidate in
                  candidate.accessories.contains { $0.uniqueIdentifier == accessoryUUID }
              }),
              let accessory = home.accessories.first(where: { $0.uniqueIdentifier == accessoryUUID }) else {
            reply(ToolResponse.error(request, code: -32603, "Accessory \(accessoryUUID.uuidString) not found"))
            return
        }
        guard let room = homeRooms(home).first(where: { $0.uniqueIdentifier == args.roomUUID }) else {
            reply(ToolResponse.error(request, code: -32603,
                                     "Room \(args.roomUUID.uuidString) not found in home \(home.name)"))
            return
        }

        let structured: [String: Any] = [
            "accessory_uuid": accessory.uniqueIdentifier.uuidString,
            "room_uuid": room.uniqueIdentifier.uuidString,
            "room": room.name
        ]
        let currentRoom = accessory.room ?? home.roomForEntireHome()
        if currentRoom.uniqueIdentifier == room.uniqueIdentifier {
            let text = "\(accessory.name) is already in \(room.name)"
            reply(ToolResponse.structured(request, text: text, structured))
            return
        }

        let accessoryName = accessory.name
        let roomName = room.name
        home.assignAccessory(accessory, to: room) { error in
            Self.finish(error, request, reply: reply, failure: "Failed to move \(accessoryName) to \(roomName)") {
                ToolResponse.structured(request, text: "Moved \(accessoryName) to \(roomName)", structured)
            }
        }
    }

    private func createRoom(_ request: MCPRequest, _ args: CreateRoomArguments, reply: @escaping Reply) {
        guard let home = homeManager.homes.first(where: { $0.uniqueIdentifier == args.homeUUID }) else {
            reply(ToolResponse.error(request, code: -32603, "Home \(args.homeUUID.uuidString) not found"))
            return
        }

        let key = ToolArguments.roomNameKey(args.name)
        if let existing = homeRooms(home).first(where: { ToolArguments.roomNameKey($0.name) == key }) {
            var info = Self.roomInfo(existing, in: home)
            info["created"] = false
            reply(ToolResponse.structured(request, text: "Room \(existing.name) already exists in \(home.name)",
                                          ["room": info]))
            return
        }

        home.addRoom(withName: args.name) { room, error in
            DispatchQueue.main.async {
                if let error = error {
                    reply(ToolResponse.error(request, code: -32603,
                                             "Failed to create room \(args.name): \(error.localizedDescription)"))
                    return
                }
                guard let room = room else {
                    reply(ToolResponse.error(request, code: -32603, "HomeKit returned no room for \(args.name)"))
                    return
                }
                var info = Self.roomInfo(room, in: home)
                info["created"] = true
                reply(ToolResponse.structured(request, text: "Created room \(room.name) in \(home.name)",
                                              ["room": info]))
            }
        }
    }

    private func getAccessoryByName(_ request: MCPRequest, _ name: String) -> MCPResponse {
        let needle = name.lowercased()
        var found: [[String: Any]] = []
        var lines: [String] = []

        for home in homeManager.homes {
            for accessory in home.accessories where accessory.name.lowercased().contains(needle) {
                let category = accessory.category.localizedDescription
                let room = accessory.room?.name ?? "No Room"
                found.append([
                    "name": accessory.name,
                    "room": room,
                    "uuid": accessory.uniqueIdentifier.uuidString,
                    "home": home.name,
                    "category": category,
                    "reachable": accessory.isReachable
                ])
                lines.append("• \(accessory.name) (\(category)) - Room: \(room), "
                             + "UUID: \(accessory.uniqueIdentifier.uuidString)")
            }
        }

        let text = found.isEmpty
            ? "No accessories found matching '\(name)'"
            : "Found \(found.count) accessories matching '\(name)':\n" + lines.joined(separator: "\n")
        return ToolResponse.text(request, text, meta: ["accessories": found])
    }

    private func getRoomByName(_ request: MCPRequest, _ name: String) -> MCPResponse {
        let needle = name.lowercased()
        var found: [[String: Any]] = []

        for home in homeManager.homes {
            for room in home.rooms where room.name.lowercased().contains(needle) {
                found.append(["name": room.name, "uuid": room.uniqueIdentifier.uuidString, "home": home.name])
            }
        }

        let text = found.isEmpty
            ? "No rooms found matching '\(name)'"
            : "Found \(found.count) rooms matching '\(name)':\n"
                + found.map { "• \($0["name"] as! String) (UUID: \($0["uuid"] as! String))" }.joined(separator: "\n")
        return ToolResponse.text(request, text, meta: ["rooms": found])
    }

    private func setAccessoryRoomByName(_ request: MCPRequest, _ arguments: [String: Any],
                                        reply: @escaping Reply) throws {
        let accessoryName = try ToolArguments.string(arguments, "accessory_name")
        let roomName = try ToolArguments.string(arguments, "room_name")
        guard let accessoryMatch = findAccessory(named: accessoryName) else {
            reply(ToolResponse.error(request, code: -32603, "Accessory '\(accessoryName)' not found"))
            return
        }
        guard let room = findRoom(named: roomName)?.room else {
            reply(ToolResponse.error(request, code: -32603, "Room '\(roomName)' not found"))
            return
        }
        let accessory = accessoryMatch.accessory
        if accessory.room?.uniqueIdentifier == room.uniqueIdentifier {
            reply(ToolResponse.text(request, "\(accessory.name) is already in \(room.name)"))
            return
        }

        let movedName = accessory.name
        let targetName = room.name
        accessoryMatch.home.assignAccessory(accessory, to: room) { error in
            Self.finish(error, request, reply: reply, failure: "Failed to move \(movedName) to \(targetName)") {
                ToolResponse.text(request, "Moved \(movedName) to \(targetName)")
            }
        }
    }

    private func renameAccessory(_ request: MCPRequest, _ arguments: [String: Any], reply: @escaping Reply) throws {
        let accessoryName = try ToolArguments.string(arguments, "accessory_name")
        let newName = try ToolArguments.string(arguments, "new_name")
        guard let accessory = findAccessory(named: accessoryName)?.accessory else {
            reply(ToolResponse.error(request, code: -32603, "Accessory '\(accessoryName)' not found"))
            return
        }

        let oldName = accessory.name
        accessory.updateName(newName) { error in
            Self.finish(error, request, reply: reply, failure: "Failed to rename '\(oldName)' to '\(newName)'") {
                ToolResponse.text(request, "Renamed '\(oldName)' to '\(newName)'")
            }
        }
    }

    private func renameRoom(_ request: MCPRequest, _ arguments: [String: Any], reply: @escaping Reply) throws {
        let roomName = try ToolArguments.string(arguments, "room_name")
        let newName = try ToolArguments.string(arguments, "new_name")
        guard let room = findRoom(named: roomName)?.room else {
            reply(ToolResponse.error(request, code: -32603, "Room '\(roomName)' not found"))
            return
        }

        let oldName = room.name
        room.updateName(newName) { error in
            Self.finish(error, request, reply: reply, failure: "Failed to rename '\(oldName)' to '\(newName)'") {
                ToolResponse.text(request, "Renamed '\(oldName)' to '\(newName)'")
            }
        }
    }

    private func getRoomAccessories(_ request: MCPRequest, _ roomName: String) -> MCPResponse {
        guard let match = findRoom(named: roomName) else {
            return ToolResponse.error(request, code: -32603, "Room '\(roomName)' not found")
        }
        let room = match.room
        let home = match.home

        var found: [[String: Any]] = []
        var lines: [String] = []
        for accessory in home.accessories where accessory.room?.uniqueIdentifier == room.uniqueIdentifier {
            let category = accessory.category.localizedDescription
            let serial = Self.informationValue(accessory, HMCharacteristicTypeSerialNumber)
            let firmware = Self.informationValue(accessory, HMCharacteristicTypeFirmwareVersion)
            found.append([
                "name": accessory.name,
                "uuid": accessory.uniqueIdentifier.uuidString,
                "category": category,
                "reachable": accessory.isReachable,
                "firmware": Self.jsonValue(firmware),
                "serial_number": Self.jsonValue(serial)
            ])
            let status = accessory.isReachable ? "🟢" : "🔴"
            lines.append("• \(accessory.name) (\(category)) \(status) - "
                         + "UUID: \(accessory.uniqueIdentifier.uuidString), "
                         + "FW: \(firmware ?? "unknown"), S/N: \(serial ?? "unknown")")
        }

        let text = found.isEmpty
            ? "No accessories found in room '\(room.name)'"
            : "Found \(found.count) accessories in '\(room.name)':\n" + lines.joined(separator: "\n")
        return ToolResponse.text(request, text, meta: [
            "room": ["name": room.name, "uuid": room.uniqueIdentifier.uuidString],
            "accessories": found
        ])
    }

    private enum AccessoryAction {
        case turnOn, turnOff, toggle
    }

    private enum ControlKind {
        case power, position, door
    }

    private struct ControlTarget {
        let value: Any
        let description: String
    }

    private static func controllable(_ accessory: HMAccessory) -> (characteristic: HMCharacteristic,
                                                                    kind: ControlKind)? {
        let kinds: [(type: String, kind: ControlKind)] = [
            (type: HMCharacteristicTypePowerState, kind: .power),
            (type: HMCharacteristicTypeTargetPosition, kind: .position),
            (type: HMCharacteristicTypeTargetDoorState, kind: .door)
        ]
        for service in accessory.services {
            for candidate in kinds {
                if let characteristic = service.characteristics.first(where: {
                    $0.characteristicType == candidate.type
                }) {
                    return (characteristic: characteristic, kind: candidate.kind)
                }
            }
        }
        return nil
    }

    private static func target(for action: AccessoryAction, kind: ControlKind, current: Any?) -> ControlTarget? {
        let doorOpen = HMCharacteristicValueDoorState.open.rawValue
        let doorClosed = HMCharacteristicValueDoorState.closed.rawValue
        switch (action, kind) {
        case (.turnOn, .power):
            return ControlTarget(value: true, description: "turn on")
        case (.turnOff, .power):
            return ControlTarget(value: false, description: "turn off")
        case (.turnOn, .position):
            return ControlTarget(value: 100, description: "open")
        case (.turnOff, .position):
            return ControlTarget(value: 0, description: "close")
        case (.turnOn, .door):
            return ControlTarget(value: doorOpen, description: "open")
        case (.turnOff, .door):
            return ControlTarget(value: doorClosed, description: "close")
        case (.toggle, .power):
            guard let isOn = current as? Bool else {
                return nil
            }
            return target(for: isOn ? .turnOff : .turnOn, kind: kind, current: nil)
        case (.toggle, .position):
            guard let position = current as? Int else {
                return nil
            }
            return target(for: position > 50 ? .turnOff : .turnOn, kind: kind, current: nil)
        case (.toggle, .door):
            guard let door = current as? Int else {
                return nil
            }
            return target(for: door == doorClosed ? .turnOn : .turnOff, kind: kind, current: nil)
        }
    }

    private func control(_ request: MCPRequest, _ accessoryName: String, action: AccessoryAction,
                         reply: @escaping Reply) {
        guard let accessory = findAccessory(named: accessoryName)?.accessory else {
            reply(ToolResponse.error(request, code: -32603, "Accessory '\(accessoryName)' not found"))
            return
        }
        guard let control = Self.controllable(accessory) else {
            reply(ToolResponse.error(request, code: -32603,
                                     "Accessory '\(accessory.name)' has no controllable characteristics"))
            return
        }
        guard let target = Self.target(for: action, kind: control.kind, current: control.characteristic.value) else {
            reply(ToolResponse.error(request, code: -32603, "Cannot read current state of '\(accessory.name)'"))
            return
        }

        let name = accessory.name
        let description = target.description
        control.characteristic.writeValue(target.value) { error in
            Self.finish(error, request, reply: reply, failure: "Failed to \(description) '\(name)'") {
                ToolResponse.text(request, "Done: \(description) '\(name)'")
            }
        }
    }
}
