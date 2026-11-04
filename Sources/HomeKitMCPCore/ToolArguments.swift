import Foundation

public enum ToolArgumentError: LocalizedError, Equatable {
    case missing(String)
    case empty(String)
    case invalidUUID(String)

    public var errorDescription: String? {
        switch self {
        case .missing(let key):
            return "Missing '\(key)' parameter"
        case .empty(let key):
            return "'\(key)' must not be empty"
        case .invalidUUID(let key):
            return "'\(key)' is not a valid UUID"
        }
    }
}

public enum ToolArguments {
    public static func string(_ arguments: [String: Any], _ key: String) throws -> String {
        guard let raw = arguments[key] as? String else {
            throw ToolArgumentError.missing(key)
        }
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            throw ToolArgumentError.empty(key)
        }
        return value
    }

    public static func uuid(_ arguments: [String: Any], _ key: String) throws -> UUID {
        let value = try string(arguments, key)
        guard let uuid = UUID(uuidString: value) else {
            throw ToolArgumentError.invalidUUID(key)
        }
        return uuid
    }

    public static func roomNameKey(_ name: String) -> String {
        name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }
}

public struct CreateRoomArguments: Equatable {
    public let homeUUID: UUID
    public let name: String

    public init(_ arguments: [String: Any]) throws {
        homeUUID = try ToolArguments.uuid(arguments, "home_uuid")
        name = try ToolArguments.string(arguments, "name")
    }
}

public struct SetAccessoryRoomArguments: Equatable {
    public let accessoryUUID: UUID
    public let roomUUID: UUID

    public init(_ arguments: [String: Any]) throws {
        accessoryUUID = try ToolArguments.uuid(arguments, "accessory_uuid")
        roomUUID = try ToolArguments.uuid(arguments, "room_uuid")
    }
}
