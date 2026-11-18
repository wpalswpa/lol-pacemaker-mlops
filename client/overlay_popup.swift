// 게임 중 오버레이 팝업 (AGENTS.md 3-4절). client/bridge.py --launch-popup 이 컴파일해 실행한다.
// 사용: overlay-popup <URL> [폭] [높이]
// 테두리 없는 창 모드의 LoL 을 눌러도 뒤로 숨지 않도록 .floating 단계의 패널로 띄우고,
// 패널을 눌러도 게임에서 입력 초점을 빼앗지 않는다(nonactivatingPanel).
import Cocoa
import WebKit

final class Panel: NSPanel {
    override var canBecomeKey: Bool { true }  // 데모 버튼·토글을 누를 수 있게
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    let url: URL
    let width: CGFloat
    let height: CGFloat
    var panel: Panel!

    init(url: URL, width: CGFloat, height: CGFloat) {
        self.url = url
        self.width = width
        self.height = height
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let margin: CGFloat = 16
        let frame = NSRect(x: screen.maxX - width - margin, y: screen.maxY - height - margin,
                           width: width, height: height)
        panel = Panel(contentRect: frame,
                      styleMask: [.titled, .closable, .resizable, .utilityWindow, .nonactivatingPanel],
                      backing: .buffered, defer: false)
        panel.title = "LoL 페이스메이커 (페이커)"
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.isMovableByWindowBackground = true
        panel.backgroundColor = NSColor(red: 0.04, green: 0.05, blue: 0.07, alpha: 1)

        let web = WKWebView(frame: panel.contentView!.bounds, configuration: WKWebViewConfiguration())
        web.autoresizingMask = [.width, .height]
        web.setValue(false, forKey: "drawsBackground")
        web.load(URLRequest(url: url))
        panel.contentView!.addSubview(web)
        panel.orderFrontRegardless()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let args = CommandLine.arguments
guard args.count >= 2, let target = URL(string: args[1]), ["http", "https"].contains(target.scheme ?? "") else {
    FileHandle.standardError.write("사용: overlay-popup <http(s) URL> [폭] [높이]\n".data(using: .utf8)!)
    exit(2)
}
let size = (w: args.count > 2 ? CGFloat(Double(args[2]) ?? 380) : 380,
            h: args.count > 3 ? CGFloat(Double(args[3]) ?? 560) : 560)

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // Dock 아이콘 없이
let delegate = AppDelegate(url: target, width: size.w, height: size.h)
app.delegate = delegate
app.run()
