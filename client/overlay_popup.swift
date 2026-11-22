// 게임 중 투명 오버레이 팝업 (AGENTS.md 3-4절). client/bridge.py --launch-popup 이 컴파일해 실행한다.
// 사용: overlay-popup <URL> [폭] [높이]
// 테두리 없는 창 모드의 LoL 위에 창 틀(.borderless)과 바깥 사각 배경(backgroundColor = .clear) 없이
// 위젯 카드만 뜨도록 .floating 단계의 투명 패널로 띄우고, 패널을 눌러도 게임에서 입력 초점을 빼앗지 않는다(nonactivatingPanel).
// 상단 드래그 바 이동(dx, dy)과 종료(close)를 WKScriptMessageHandler("macHud")로 처리한다.
import Cocoa
import WebKit

final class Panel: NSPanel {
    override var canBecomeKey: Bool { true }  // 데모 버튼·토글을 누를 수 있게
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler {
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
        let margin: CGFloat = 18
        let frame = NSRect(x: screen.maxX - width - margin, y: screen.maxY - height - 56,
                           width: width, height: height)
        panel = Panel(contentRect: frame,
                      styleMask: [.borderless, .nonactivatingPanel],
                      backing: .buffered, defer: false)
        panel.title = "LoL 페이스메이커"
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        panel.isFloatingPanel = true
        panel.isMovableByWindowBackground = true
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "macHud")
        let web = WKWebView(frame: panel.contentView!.bounds, configuration: config)
        web.autoresizingMask = [.width, .height]
        web.setValue(false, forKey: "drawsBackground")
        web.load(URLRequest(url: url))
        panel.contentView!.addSubview(web)
        panel.orderFrontRegardless()
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let p = panel else { return }
        if let close = body["close"] as? Bool, close {
            NSApplication.shared.terminate(nil)
            return
        }
        if body["dx"] != nil || body["dy"] != nil {
            let dx = CGFloat((body["dx"] as? NSNumber)?.doubleValue ?? 0)
            let dy = CGFloat((body["dy"] as? NSNumber)?.doubleValue ?? 0)
            var origin = p.frame.origin
            origin.x += dx
            origin.y -= dy  // Cocoa 좌표계는 아래에서 위로 증가
            p.setFrameOrigin(origin)
        }
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
