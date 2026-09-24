// mr-audio-helper: the macOS-only pieces of Meeting Recorder.
//
//   mr-audio-helper capture [--rate 16000]
//       Streams system audio (everything the Mac plays) as raw mono float32
//       little-endian samples on stdout, using ScreenCaptureKit. Prints READY on
//       stderr once audio is flowing. Stops when stdin closes.
//       Exit codes: 0 stopped normally, 3 permission denied, 2 other failure.
//   mr-audio-helper permission           prints "granted" or "denied"
//   mr-audio-helper request-permission   shows the macOS prompt (first time only)
//   mr-audio-helper mic-users            bundle IDs of apps using a microphone now
//                                        (macOS 14+; prints nothing on 13)
//   mr-audio-helper version
//
// Build: swiftc -O -parse-as-library AudioHelper.swift -o mr-audio-helper

import AVFoundation
import CoreAudio
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

let helperVersion = "1"

func log(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

final class SystemAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    private let stdout = FileHandle.standardOutput
    private let sampleQueue = DispatchQueue(label: "mr.audio")
    private var stream: SCStream?
    private var warnedFormat = false
    let sampleRate: Int

    init(sampleRate: Int) {
        self.sampleRate = sampleRate
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            throw NSError(domain: "mr", code: 2, userInfo: [NSLocalizedDescriptionKey: "No display found"])
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = sampleRate
        config.channelCount = 1
        // ScreenCaptureKit always captures video too. Ask for the smallest, slowest
        // video it allows and ignore the frames.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: sampleQueue)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: sampleQueue)
        try await stream.startCapture()
        self.stream = stream
    }

    func stop() async {
        try? await stream?.stopCapture()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid else { return }
        guard let samples = monoFloats(sampleBuffer), !samples.isEmpty else { return }
        samples.withUnsafeBytes { stdout.write(Data($0)) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        log("ERROR capture stopped: \(error.localizedDescription)")
        exit(2)
    }

    /// Mixes whatever layout ScreenCaptureKit delivered down to one float32 channel.
    private func monoFloats(_ sampleBuffer: CMSampleBuffer) -> [Float]? {
        if let format = CMSampleBufferGetFormatDescription(sampleBuffer),
           let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee,
           asbd.mFormatFlags & kAudioFormatFlagIsFloat == 0 || asbd.mBitsPerChannel != 32 {
            if !warnedFormat {
                log("ERROR unexpected audio format: flags \(asbd.mFormatFlags), bits \(asbd.mBitsPerChannel)")
                warnedFormat = true
            }
            return nil
        }

        var sizeNeeded = 0
        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer, bufferListSizeNeededOut: &sizeNeeded, bufferListOut: nil, bufferListSize: 0,
            blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: nil)
        guard sizeNeeded > 0 else { return nil }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: sizeNeeded,
                                                   alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { raw.deallocate() }
        let listPointer = raw.bindMemory(to: AudioBufferList.self, capacity: 1)
        var blockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer, bufferListSizeNeededOut: nil, bufferListOut: listPointer,
            bufferListSize: sizeNeeded, blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
            flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment),
            blockBufferOut: &blockBuffer)
        guard status == noErr else { return nil }

        let buffers = UnsafeMutableAudioBufferListPointer(listPointer)
        let frames = CMSampleBufferGetNumSamples(sampleBuffer)
        var mono = [Float](repeating: 0, count: frames)
        if buffers.count == 1 {
            // One buffer, possibly interleaved.
            let channels = max(Int(buffers[0].mNumberChannels), 1)
            guard let data = buffers[0].mData else { return nil }
            let p = data.assumingMemoryBound(to: Float.self)
            for i in 0..<frames {
                var sum: Float = 0
                for c in 0..<channels { sum += p[i * channels + c] }
                mono[i] = sum / Float(channels)
            }
        } else {
            // One buffer per channel.
            let scale = 1 / Float(buffers.count)
            for buffer in buffers {
                guard let data = buffer.mData else { continue }
                let p = data.assumingMemoryBound(to: Float.self)
                for i in 0..<frames { mono[i] += p[i] * scale }
            }
        }
        return mono
    }
}

func isPermissionError(_ error: Error) -> Bool {
    let ns = error as NSError
    // SCStreamError.userDeclined, or the TCC denial that surfaces before a stream exists.
    return (ns.domain == SCStreamErrorDomain && ns.code == SCStreamError.userDeclined.rawValue)
        || !CGPreflightScreenCaptureAccess()
}

func runCapture(sampleRate: Int) async -> Int32 {
    let capture = SystemAudioCapture(sampleRate: sampleRate)
    do {
        try await capture.start()
    } catch {
        if isPermissionError(error) {
            log("PERMISSION_DENIED \(error.localizedDescription)")
            return 3
        }
        log("ERROR \(error.localizedDescription)")
        return 2
    }
    log("READY")
    // Run until the parent closes our stdin (or dies, which closes it too).
    await withCheckedContinuation { (done: CheckedContinuation<Void, Never>) in
        DispatchQueue.global().async {
            _ = FileHandle.standardInput.readDataToEndOfFile()
            done.resume()
        }
    }
    await capture.stop()
    return 0
}

@available(macOS 14.0, *)
func appsUsingMicrophone() -> [String] {
    let system = AudioObjectID(kAudioObjectSystemObject)
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyProcessObjectList,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr, size > 0 else { return [] }
    var processes = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &processes) == noErr else { return [] }

    var bundleIDs: [String] = []
    for process in processes {
        var running: UInt32 = 0
        var runningSize = UInt32(MemoryLayout<UInt32>.size)
        var runningAddress = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyIsRunningInput,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectGetPropertyData(process, &runningAddress, 0, nil, &runningSize, &running) == noErr,
              running != 0 else { continue }

        var bundleRef: Unmanaged<CFString>?
        var bundleSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        var bundleAddress = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyBundleID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        let status = withUnsafeMutablePointer(to: &bundleRef) {
            AudioObjectGetPropertyData(process, &bundleAddress, 0, nil, &bundleSize, $0)
        }
        if status == noErr, let id = bundleRef?.takeRetainedValue() as String?, !id.isEmpty {
            bundleIDs.append(id)
        }
    }
    return bundleIDs
}

@main
struct AudioHelper {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        switch args.first {
        case "capture":
            var rate = 16000
            if let i = args.firstIndex(of: "--rate"), i + 1 < args.count, let r = Int(args[i + 1]) {
                rate = r
            }
            exit(await runCapture(sampleRate: rate))
        case "permission":
            print(CGPreflightScreenCaptureAccess() ? "granted" : "denied")
        case "request-permission":
            print(CGRequestScreenCaptureAccess() ? "granted" : "denied")
        case "mic-users":
            if #available(macOS 14.0, *) {
                for id in appsUsingMicrophone() { print(id) }
            }
        case "version":
            print(helperVersion)
        default:
            log("usage: mr-audio-helper capture [--rate N] | permission | request-permission | mic-users | version")
            exit(64)
        }
    }
}
