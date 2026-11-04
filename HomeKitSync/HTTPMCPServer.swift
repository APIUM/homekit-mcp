import Foundation
import Network

enum ServerStatus: Equatable {
    case starting
    case ready
    case failed(String)
    case stopped
}

final class HTTPMCPServer {
    static let port: UInt16 = 8080

    private struct Client {
        let connection: NWConnection
        var buffer = HTTPRequestBuffer()
    }

    private let tools = HomeKitTools()
    private let onStatus: (ServerStatus) -> Void
    private let encoder = JSONEncoder()
    private var listener: NWListener?
    private var clients: [ObjectIdentifier: Client] = [:]

    init(onStatus: @escaping (ServerStatus) -> Void) {
        self.onStatus = onStatus
    }

    deinit {
        shutdown()
    }

    func start() {
        guard listener == nil else {
            return
        }
        guard let port = NWEndpoint.Port(rawValue: Self.port) else {
            onStatus(.failed("Invalid port \(Self.port)"))
            return
        }
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        parameters.requiredInterfaceType = .loopback
        parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback), port: port)

        let listener: NWListener
        do {
            listener = try NWListener(using: parameters)
        } catch {
            onStatus(.failed("\(error)"))
            return
        }
        listener.stateUpdateHandler = { [weak self] state in
            self?.listenerStateChanged(state)
        }
        listener.newConnectionHandler = { [weak self] connection in
            self?.accept(connection)
        }
        self.listener = listener
        onStatus(.starting)
        listener.start(queue: .main)
    }

    func stop() {
        shutdown()
        onStatus(.stopped)
    }

    private func shutdown() {
        listener?.stateUpdateHandler = nil
        listener?.newConnectionHandler = nil
        listener?.cancel()
        listener = nil
        let openConnections = clients.values.map { $0.connection }
        clients.removeAll()
        for connection in openConnections {
            connection.stateUpdateHandler = nil
            connection.cancel()
        }
    }

    private func listenerStateChanged(_ state: NWListener.State) {
        switch state {
        case .ready:
            print("HTTP MCP Server listening on 127.0.0.1:\(Self.port)")
            onStatus(.ready)
        case .failed(let error), .waiting(let error):
            print("HTTP Server failed: \(error)")
            shutdown()
            onStatus(.failed("\(error)"))
        default:
            break
        }
    }

    private func accept(_ connection: NWConnection) {
        guard LoopbackEndpoint.isLoopback(connection.endpoint) else {
            print("Rejected non-loopback connection from \(connection.endpoint)")
            connection.cancel()
            return
        }
        let clientID = ObjectIdentifier(connection)
        clients[clientID] = Client(connection: connection)
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                self?.receive(clientID)
            case .failed(let error):
                print("Connection failed: \(error)")
                connection.cancel()
            case .cancelled:
                connection.stateUpdateHandler = nil
                self?.clients[clientID] = nil
            default:
                break
            }
        }
        connection.start(queue: .main)
    }

    private func receive(_ clientID: ObjectIdentifier) {
        guard let connection = clients[clientID]?.connection else {
            return
        }
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, done, error in
            guard let self = self else {
                return
            }
            if let error = error {
                print("Receive error: \(error)")
                connection.cancel()
                return
            }
            if let data = data, !data.isEmpty, let result = self.clients[clientID]?.buffer.append(data) {
                switch result {
                case .complete(let request):
                    self.clients[clientID]?.buffer = HTTPRequestBuffer()
                    self.handle(request, on: connection)
                    return
                case .failed(let failure):
                    self.send(HTTPResponse.failure(failure), on: connection)
                    return
                case .incomplete:
                    break
                }
            }
            if done {
                if let buffer = self.clients[clientID]?.buffer, !buffer.isEmpty {
                    self.send(HTTPResponse.failure(buffer.endOfStream()), on: connection)
                } else {
                    connection.cancel()
                }
                return
            }
            self.receive(clientID)
        }
    }

    private func handle(_ request: HTTPRequest, on connection: NWConnection) {
        print("📥 [HTTP] \(request.method) \(request.path)")
        if let failure = HTTPRequestPolicy.check(request, port: Self.port) {
            print("❌ [HTTP] \(failure.status) \(failure.message)")
            send(HTTPResponse.failure(failure), on: connection)
            return
        }

        switch (request.method, request.path) {
        case ("GET", "/events"):
            startEventStream(on: connection)
        case ("GET", "/mcp"):
            sendJSONObject(discovery(), on: connection)
        case ("POST", "/mcp"):
            handleRPC(request.body, on: connection) { rpc, reply in
                self.process(rpc, reply: reply)
            }
        case ("POST", "/mcp/initialize"):
            handleRPC(request.body, on: connection) { rpc, reply in
                reply(self.initializeResponse(rpc))
            }
        case ("POST", "/mcp/tools/list"):
            handleRPC(request.body, on: connection) { rpc, reply in
                reply(self.toolsListResponse(rpc))
            }
        case ("POST", "/mcp/tools/call"):
            handleRPC(request.body, on: connection) { rpc, reply in
                self.tools.call(rpc, reply: reply)
            }
        case ("GET", "/"):
            send(HTTPResponse.serialize(status: 200, contentType: "text/html; charset=utf-8",
                                        body: Data(welcomeHTML().utf8)), on: connection)
        default:
            send(HTTPResponse.failure(HTTPFailure(status: 404, message: "Not Found")), on: connection)
        }
    }

    private func handleRPC(_ body: Data, on connection: NWConnection,
                           _ handler: (MCPRequest, @escaping (MCPResponse) -> Void) -> Void) {
        let reply: (MCPResponse) -> Void = { [weak self] response in
            self?.sendRPC(response, on: connection)
        }
        let parsed = try? JSONSerialization.jsonObject(with: body)
        guard parsed != nil else {
            reply(MCPResponse(jsonrpc: "2.0", id: nil, result: nil,
                              error: MCPError(code: -32700, message: "Parse error")))
            return
        }
        guard let request = try? JSONDecoder().decode(MCPRequest.self, from: body) else {
            reply(MCPResponse(jsonrpc: "2.0", id: nil, result: nil,
                              error: MCPError(code: -32600, message: "Invalid Request")))
            return
        }
        handler(request, reply)
    }

    private func process(_ request: MCPRequest, reply: @escaping (MCPResponse) -> Void) {
        switch request.method {
        case "initialize":
            reply(initializeResponse(request))
        case "tools/list":
            reply(toolsListResponse(request))
        case "tools/call":
            tools.call(request, reply: reply)
        default:
            reply(MCPResponse(jsonrpc: "2.0", id: request.id, result: nil,
                              error: MCPError(code: -32601, message: "Method not found")))
        }
    }

    private func serverInfo() -> [String: Any] {
        ["name": "homekit-mcp-server", "version": "1.0.0"]
    }

    private func discovery() -> [String: Any] {
        ["version": "2024-11-05", "capabilities": ["tools": [String: Any]()], "serverInfo": serverInfo()]
    }

    private func initializeResponse(_ request: MCPRequest) -> MCPResponse {
        MCPResponse(jsonrpc: "2.0", id: request.id,
                    result: [
                        "protocolVersion": AnyEncodable("2024-11-05"),
                        "capabilities": AnyEncodable(["tools": [String: Any]()]),
                        "serverInfo": AnyEncodable(serverInfo())
                    ], error: nil)
    }

    private func toolsListResponse(_ request: MCPRequest) -> MCPResponse {
        MCPResponse(jsonrpc: "2.0", id: request.id, result: ["tools": AnyEncodable(ToolCatalog.json)], error: nil)
    }

    private func sendRPC(_ response: MCPResponse, on connection: NWConnection) {
        do {
            let body = try encoder.encode(response)
            print("✅ [MCP] Sending response for ID \(response.id ?? -1) (\(body.count) bytes)")
            send(HTTPResponse.serialize(status: 200, contentType: "application/json", body: body), on: connection)
        } catch {
            print("❌ [MCP] Failed to encode response: \(error)")
            send(HTTPResponse.failure(HTTPFailure(status: 500, message: "Internal Server Error")), on: connection)
        }
    }

    private func sendJSONObject(_ object: [String: Any], on connection: NWConnection) {
        guard let body = try? JSONSerialization.data(withJSONObject: object) else {
            send(HTTPResponse.failure(HTTPFailure(status: 500, message: "Internal Server Error")), on: connection)
            return
        }
        send(HTTPResponse.serialize(status: 200, contentType: "application/json", body: body), on: connection)
    }

    private func send(_ data: Data, on connection: NWConnection) {
        connection.send(content: data, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }

    private func startEventStream(on connection: NWConnection) {
        let headers = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
            + "Connection: keep-alive\r\n\r\n"
        var info = serverInfo()
        info["tools"] = ToolCatalog.all.map { $0.name }
        guard let json = try? JSONSerialization.data(withJSONObject: info),
              let text = String(data: json, encoding: .utf8) else {
            send(HTTPResponse.failure(HTTPFailure(status: 500, message: "Internal Server Error")), on: connection)
            return
        }
        let stream = headers + "event: server-info\ndata: \(text)\n\n"
        connection.send(content: Data(stream.utf8), completion: .contentProcessed { error in
            if let error = error {
                print("Failed to send SSE event: \(error)")
                connection.cancel()
            }
        })
        Self.cancelWhenClosed(connection)
    }

    private static func cancelWhenClosed(_ connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 4096) { _, _, done, error in
            if done || error != nil {
                connection.cancel()
            } else {
                Self.cancelWhenClosed(connection)
            }
        }
    }

    private func welcomeHTML() -> String {
        let tools = ToolCatalog.all
            .map { "<li><code>\($0.name)</code> - \($0.description)</li>" }
            .joined(separator: "\n")
        return """
        <!DOCTYPE html>
        <html>
        <head><title>HomeKit MCP Server</title></head>
        <body>
            <h1>HomeKit MCP Server</h1>
            <p>Listening on 127.0.0.1:\(Self.port). Requests must not send an Origin header, must use Host
            127.0.0.1:\(Self.port) or localhost:\(Self.port), and POST bodies must be application/json.</p>
            <h2>Endpoints</h2>
            <ul>
                <li><code>GET /mcp</code> - MCP server discovery</li>
                <li><code>POST /mcp</code> - MCP JSON-RPC requests</li>
                <li><code>POST /mcp/initialize</code> - Initialize MCP session</li>
                <li><code>POST /mcp/tools/list</code> - List available tools</li>
                <li><code>POST /mcp/tools/call</code> - Execute a tool</li>
                <li><code>GET /events</code> - Server-Sent Events stream</li>
            </ul>
            <h2>Tools</h2>
            <ul>
            \(tools)
            </ul>
        </body>
        </html>
        """
    }
}
