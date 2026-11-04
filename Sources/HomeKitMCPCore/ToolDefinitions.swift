import Foundation

public struct ToolDefinition {
    public let name: String
    public let description: String
    public let parameters: [(name: String, description: String)]

    public var json: [String: Any] {
        var properties: [String: Any] = [:]
        for parameter in parameters {
            properties[parameter.name] = ["type": "string", "description": parameter.description]
        }
        return [
            "name": name,
            "description": description,
            "inputSchema": [
                "type": "object",
                "properties": properties,
                "required": parameters.map { $0.name }
            ] as [String: Any]
        ]
    }
}

public enum ToolCatalog {
    public static let all: [ToolDefinition] = [
        ToolDefinition(name: "get_all_accessories",
                       description: "Get all HomeKit accessories with their rooms, bridges, serial numbers and UUIDs",
                       parameters: []),
        ToolDefinition(name: "get_all_rooms",
                       description: "Get all HomeKit rooms, including each home's default room, with their UUIDs",
                       parameters: []),
        ToolDefinition(name: "set_accessory_room",
                       description: "Move an accessory to a different room using UUIDs",
                       parameters: [("accessory_uuid", "UUID of the accessory to move"),
                                    ("room_uuid", "UUID of the target room")]),
        ToolDefinition(name: "create_room",
                       description: "Create a room in a home, or return the existing room with the same name",
                       parameters: [("home_uuid", "UUID of the home to create the room in"),
                                    ("name", "Room name, matched case-insensitively against existing rooms")]),
        ToolDefinition(name: "get_accessory_by_name",
                       description: "Find a HomeKit accessory by name",
                       parameters: [("name", "Name or partial name of the accessory to find")]),
        ToolDefinition(name: "get_room_by_name",
                       description: "Find a HomeKit room by name",
                       parameters: [("name", "Name or partial name of the room to find")]),
        ToolDefinition(name: "set_accessory_room_by_name",
                       description: "Move an accessory to a different room using names",
                       parameters: [("accessory_name", "Name of the accessory to move"),
                                    ("room_name", "Name of the target room")]),
        ToolDefinition(name: "rename_accessory",
                       description: "Rename a HomeKit accessory",
                       parameters: [("accessory_name", "Current name of the accessory to rename"),
                                    ("new_name", "New name for the accessory")]),
        ToolDefinition(name: "rename_room",
                       description: "Rename a HomeKit room",
                       parameters: [("room_name", "Current name of the room to rename"),
                                    ("new_name", "New name for the room")]),
        ToolDefinition(name: "get_room_accessories",
                       description: "Get all accessories in a specific room",
                       parameters: [("room_name", "Name of the room to get accessories from")]),
        ToolDefinition(name: "accessory_on",
                       description: "Turn on an accessory (lights, switches) or open covers",
                       parameters: [("accessory_name", "Name of the accessory to turn on")]),
        ToolDefinition(name: "accessory_off",
                       description: "Turn off an accessory (lights, switches) or close covers",
                       parameters: [("accessory_name", "Name of the accessory to turn off")]),
        ToolDefinition(name: "accessory_toggle",
                       description: "Toggle an accessory (lights, switches, covers) between on/off or open/close",
                       parameters: [("accessory_name", "Name of the accessory to toggle")])
    ]

    public static var json: [[String: Any]] {
        all.map { $0.json }
    }

    public static func definition(named name: String) -> ToolDefinition? {
        all.first { $0.name == name }
    }
}
