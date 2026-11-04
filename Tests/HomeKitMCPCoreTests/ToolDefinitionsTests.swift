@testable import HomeKitMCPCore
import XCTest

final class ToolDefinitionsTests: XCTestCase {
    private func encodedTools() throws -> [[String: Any]] {
        let response = MCPResponse(jsonrpc: "2.0", id: 1, result: ["tools": AnyEncodable(ToolCatalog.json)], error: nil)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(response)) as? [String: Any])
        let result = try XCTUnwrap(json["result"] as? [String: Any])
        return try XCTUnwrap(result["tools"] as? [[String: Any]])
    }

    func testToolNamesAreUnique() {
        let names = ToolCatalog.all.map { $0.name }
        XCTAssertEqual(Set(names).count, names.count)
    }

    func testEncodedSchemasAreObjectsWithRequiredProperties() throws {
        for tool in try encodedTools() {
            let name = tool["name"] as? String ?? "?"
            let schema = try XCTUnwrap(tool["inputSchema"] as? [String: Any], name)
            XCTAssertEqual(schema["type"] as? String, "object", name)
            let properties = try XCTUnwrap(schema["properties"] as? [String: Any], "\(name) properties")
            let required = try XCTUnwrap(schema["required"] as? [String], "\(name) required")
            XCTAssertEqual(Set(required), Set(properties.keys), name)
            for (key, value) in properties {
                let property = try XCTUnwrap(value as? [String: Any], "\(name).\(key)")
                XCTAssertEqual(property["type"] as? String, "string", "\(name).\(key)")
                XCTAssertFalse((property["description"] as? String ?? "").isEmpty, "\(name).\(key)")
            }
        }
    }

    func testParameterlessToolsEncodeEmptyObjectAndArray() throws {
        let tool = try XCTUnwrap(try encodedTools().first { $0["name"] as? String == "get_all_rooms" })
        let schema = try XCTUnwrap(tool["inputSchema"] as? [String: Any])
        XCTAssertEqual((schema["properties"] as? [String: Any])?.count, 0)
        XCTAssertEqual((schema["required"] as? [Any])?.count, 0)
    }

    private func validArguments(for tool: String) -> [String: Any] {
        let uuid = "6F1E3C4A-2B7D-4E8F-9A10-B2C3D4E5F601"
        let required = ToolCatalog.definition(named: tool)?.parameters.map { $0.name } ?? []
        var arguments: [String: Any] = [:]
        for key in required {
            arguments[key] = key.hasSuffix("_uuid") ? uuid : "Living Room"
        }
        return arguments
    }

    func testCreateRoomValidatorRequiresEverySchemaParameter() throws {
        XCTAssertNoThrow(try CreateRoomArguments(validArguments(for: "create_room")))
        for key in try XCTUnwrap(ToolCatalog.definition(named: "create_room")).parameters.map({ $0.name }) {
            var arguments = validArguments(for: "create_room")
            arguments.removeValue(forKey: key)
            XCTAssertThrowsError(try CreateRoomArguments(arguments)) { error in
                XCTAssertEqual(error as? ToolArgumentError, .missing(key))
            }
        }
    }

    func testSetAccessoryRoomValidatorRequiresEverySchemaParameter() throws {
        XCTAssertNoThrow(try SetAccessoryRoomArguments(validArguments(for: "set_accessory_room")))
        for key in try XCTUnwrap(ToolCatalog.definition(named: "set_accessory_room")).parameters.map({ $0.name }) {
            var arguments = validArguments(for: "set_accessory_room")
            arguments.removeValue(forKey: key)
            XCTAssertThrowsError(try SetAccessoryRoomArguments(arguments)) { error in
                XCTAssertEqual(error as? ToolArgumentError, .missing(key))
            }
        }
    }
}

final class SingleUseCallbackTests: XCTestCase {
    func testOnlyTheFirstValueIsDelivered() {
        var received: [Int] = []
        let callback = SingleUseCallback<Int> { received.append($0) }
        XCTAssertFalse(callback.isUsed)
        callback(1)
        callback(2)
        XCTAssertTrue(callback.isUsed)
        XCTAssertEqual(received, [1])
    }
}
