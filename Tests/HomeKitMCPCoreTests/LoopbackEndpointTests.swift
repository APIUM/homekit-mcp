@testable import HomeKitMCPCore
import Network
import XCTest

final class LoopbackEndpointTests: XCTestCase {
    private func endpoint(_ host: NWEndpoint.Host) -> NWEndpoint {
        .hostPort(host: host, port: 8080)
    }

    func testIPv4LoopbackIsAccepted() throws {
        let address = try XCTUnwrap(IPv4Address("127.0.0.1"))
        XCTAssertTrue(LoopbackEndpoint.isLoopback(endpoint(.ipv4(address))))
    }

    func testIPv6LoopbackIsAccepted() throws {
        let address = try XCTUnwrap(IPv6Address("::1"))
        XCTAssertTrue(LoopbackEndpoint.isLoopback(endpoint(.ipv6(address))))
    }

    func testIPv4MappedLoopbackIsAccepted() throws {
        let address = try XCTUnwrap(IPv6Address("::ffff:127.0.0.1"))
        XCTAssertTrue(LoopbackEndpoint.isLoopback(endpoint(.ipv6(address))))
    }

    func testLANAddressesAreRejected() throws {
        let ipv4 = try XCTUnwrap(IPv4Address("192.168.1.20"))
        let ipv6 = try XCTUnwrap(IPv6Address("fe80::1"))
        let mapped = try XCTUnwrap(IPv6Address("::ffff:10.0.0.5"))
        XCTAssertFalse(LoopbackEndpoint.isLoopback(endpoint(.ipv4(ipv4))))
        XCTAssertFalse(LoopbackEndpoint.isLoopback(endpoint(.ipv6(ipv6))))
        XCTAssertFalse(LoopbackEndpoint.isLoopback(endpoint(.ipv6(mapped))))
    }

    func testHostNamesAreRejected() {
        XCTAssertFalse(LoopbackEndpoint.isLoopback(endpoint(.name("localhost", nil))))
    }
}
