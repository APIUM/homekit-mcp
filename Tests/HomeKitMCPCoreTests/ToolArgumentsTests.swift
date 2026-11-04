@testable import HomeKitMCPCore
import XCTest

final class ToolArgumentsTests: XCTestCase {
    private let homeUUID = "6F1E3C4A-2B7D-4E8F-9A10-B2C3D4E5F601"
    private let accessoryUUID = "1A2B3C4D-5E6F-4071-8293-A4B5C6D7E8F9"
    private let roomUUID = "0F9E8D7C-6B5A-4938-A271-605F4E3D2C1B"

    func testCreateRoomParsesAndTrimsName() throws {
        let args = try CreateRoomArguments(["home_uuid": homeUUID, "name": "  Living Room \n"])
        XCTAssertEqual(args.homeUUID, UUID(uuidString: homeUUID))
        XCTAssertEqual(args.name, "Living Room")
    }

    func testCreateRoomAcceptsLowercaseUUID() throws {
        let args = try CreateRoomArguments(["home_uuid": homeUUID.lowercased(), "name": "Office"])
        XCTAssertEqual(args.homeUUID, UUID(uuidString: homeUUID))
    }

    func testCreateRoomRejectsMissingHome() {
        assertError(.missing("home_uuid")) { try CreateRoomArguments(["name": "Office"]) }
    }

    func testCreateRoomRejectsInvalidHomeUUID() {
        assertError(.invalidUUID("home_uuid")) {
            try CreateRoomArguments(["home_uuid": "not-a-uuid", "name": "Office"])
        }
    }

    func testCreateRoomRejectsNonStringHome() {
        assertError(.missing("home_uuid")) { try CreateRoomArguments(["home_uuid": 42, "name": "Office"]) }
    }

    func testCreateRoomRejectsMissingName() {
        assertError(.missing("name")) { try CreateRoomArguments(["home_uuid": homeUUID]) }
    }

    func testCreateRoomRejectsBlankName() {
        assertError(.empty("name")) { try CreateRoomArguments(["home_uuid": homeUUID, "name": "   "]) }
    }

    func testSetAccessoryRoomParsesUUIDs() throws {
        let args = try SetAccessoryRoomArguments(["accessory_uuid": accessoryUUID, "room_uuid": roomUUID])
        XCTAssertEqual(args.accessoryUUID, UUID(uuidString: accessoryUUID))
        XCTAssertEqual(args.roomUUID, UUID(uuidString: roomUUID))
    }

    func testSetAccessoryRoomRejectsMissingAccessory() {
        assertError(.missing("accessory_uuid")) { try SetAccessoryRoomArguments(["room_uuid": roomUUID]) }
    }

    func testSetAccessoryRoomRejectsMissingRoom() {
        assertError(.missing("room_uuid")) { try SetAccessoryRoomArguments(["accessory_uuid": accessoryUUID]) }
    }

    func testSetAccessoryRoomRejectsEmptyAccessory() {
        assertError(.empty("accessory_uuid")) {
            try SetAccessoryRoomArguments(["accessory_uuid": "", "room_uuid": roomUUID])
        }
    }

    func testSetAccessoryRoomRejectsInvalidRoomUUID() {
        assertError(.invalidUUID("room_uuid")) {
            try SetAccessoryRoomArguments(["accessory_uuid": accessoryUUID, "room_uuid": "Kitchen"])
        }
    }

    func testErrorMessagesNameTheArgument() {
        XCTAssertEqual(ToolArgumentError.missing("name").localizedDescription, "Missing 'name' parameter")
        XCTAssertEqual(ToolArgumentError.invalidUUID("room_uuid").localizedDescription,
                       "'room_uuid' is not a valid UUID")
    }

    func testRoomNameKeyIgnoresCaseAndSurroundingWhitespace() {
        XCTAssertEqual(ToolArguments.roomNameKey("  Living Room "), ToolArguments.roomNameKey("living room"))
        XCTAssertNotEqual(ToolArguments.roomNameKey("Living Room"), ToolArguments.roomNameKey("LivingRoom"))
    }

    private func assertError<T>(_ expected: ToolArgumentError, file: StaticString = #filePath, line: UInt = #line,
                                _ body: () throws -> T) {
        XCTAssertThrowsError(try body(), file: file, line: line) { error in
            XCTAssertEqual(error as? ToolArgumentError, expected, file: file, line: line)
        }
    }
}
