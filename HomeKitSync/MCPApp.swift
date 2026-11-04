import HomeKit
import SwiftUI

@main
struct HomeKitMCPApp: App {
    @StateObject private var server = AppWrapper()
    
    var body: some Scene {
        WindowGroup {
            ContentView(server: server)
                .frame(minWidth: 400, minHeight: 300)
        }
    }
}

struct ContentView: View {
    @ObservedObject var server: AppWrapper
    
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("HomeKit MCP Server")
                .font(.title)
                .bold()
            
            HStack {
                Text("Status:")
                Text(statusText)
                    .foregroundColor(statusColor)
                    .bold()
            }
            
            if server.status == .ready {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Server Details:")
                        .font(.headline)
                    
                    Text("Port: 8080")
                        .font(.body)
                    
                    Text("Available Endpoints:")
                        .font(.subheadline)
                        .bold()
                    
                    VStack(alignment: .leading, spacing: 4) {
                        Text("• GET http://127.0.0.1:8080/mcp (discovery)")
                        Text("• POST http://127.0.0.1:8080/mcp (JSON-RPC)")
                        Text("• POST http://127.0.0.1:8080/mcp/initialize")
                        Text("• POST http://127.0.0.1:8080/mcp/tools/list")
                        Text("• POST http://127.0.0.1:8080/mcp/tools/call")
                    }
                    .font(.caption)
                    .foregroundColor(.secondary)
                    
                    Text("Available MCP Tools:")
                        .font(.subheadline)
                        .bold()
                        .padding(.top)
                    
                    ScrollView {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(ToolCatalog.all, id: \.name) { tool in
                                Text("• \(tool.name) - \(tool.description)")
                            }
                        }
                    }
                    .frame(maxHeight: 120)
                    .font(.caption)
                    .foregroundColor(.secondary)
                }
                .padding()
                .background(Color.gray.opacity(0.1))
                .cornerRadius(8)
            }
            
            HStack {
                Button(server.isRunning ? "Stop Server" : "Start Server") {
                    server.toggleServer()
                }
                .buttonStyle(.borderedProminent)
                
                Spacer()
                
                Button("Quit") {
                    exit(0)
                }
                .buttonStyle(.bordered)
            }
            
            Spacer()
        }
        .padding()
    }
    
    private var statusText: String {
        switch server.status {
        case .starting:
            return "Starting"
        case .ready:
            return "Running on 127.0.0.1:\(HTTPMCPServer.port)"
        case .failed(let reason):
            return "Failed: \(reason)"
        case .stopped:
            return "Stopped"
        }
    }
    
    private var statusColor: Color {
        switch server.status {
        case .ready:
            return .green
        case .starting:
            return .orange
        case .failed, .stopped:
            return .red
        }
    }
}

final class AppWrapper: ObservableObject {
    @Published private(set) var status: ServerStatus = .stopped
    private var server: HTTPMCPServer?
    
    var isRunning: Bool {
        status == .starting || status == .ready
    }
    
    init() {
        server = HTTPMCPServer { [weak self] status in
            self?.status = status
        }
        server?.start()
    }
    
    func toggleServer() {
        if isRunning {
            server?.stop()
        } else {
            server?.start()
        }
    }
}
