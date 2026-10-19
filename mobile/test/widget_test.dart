import 'dart:typed_data';

import 'package:absen_mobile/capture/frame_converter.dart';
import 'package:absen_mobile/capture/timeline.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;

/// 4x2 BGRA frame whose red channel encodes the pixel position: red = 10 * (y * 4 + x).
RawFrame bgraGrid() {
  const w = 4, h = 2;
  final b = Uint8List(w * h * 4);
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      final i = (y * w + x) * 4;
      b[i + 2] = 10 * (y * w + x); // R
      b[i + 3] = 255;
    }
  }
  return RawFrame(width: w, height: h, format: RawFormat.bgra8888, planes: [b], rowStrides: [w * 4], pixelStrides: [4]);
}

List<List<int>> reds(img.Image im) => [
      for (var y = 0; y < im.height; y++) [for (var x = 0; x < im.width; x++) im.getPixel(x, y).r.toInt()],
    ];

void main() {
  group('frame rotation', () {
    // Source (red/10):  0 1 2 3
    //                   4 5 6 7
    test('0 degrees keeps the layout', () {
      expect(reds(toRgbImage(bgraGrid(), const FrameGeometry(rotation: 0))), [
        [0, 10, 20, 30],
        [40, 50, 60, 70],
      ]);
    });

    test('90 degrees clockwise', () {
      expect(reds(toRgbImage(bgraGrid(), const FrameGeometry(rotation: 90))), [
        [40, 0],
        [50, 10],
        [60, 20],
        [70, 30],
      ]);
    });

    test('180 degrees', () {
      expect(reds(toRgbImage(bgraGrid(), const FrameGeometry(rotation: 180))), [
        [70, 60, 50, 40],
        [30, 20, 10, 0],
      ]);
    });

    test('270 degrees clockwise', () {
      expect(reds(toRgbImage(bgraGrid(), const FrameGeometry(rotation: 270))), [
        [30, 70],
        [20, 60],
        [10, 50],
        [0, 40],
      ]);
    });

    test('mirror flips left and right after rotation', () {
      expect(reds(toRgbImage(bgraGrid(), const FrameGeometry(rotation: 0, mirror: true))), [
        [30, 20, 10, 0],
        [70, 60, 50, 40],
      ]);
    });

    test('rejects non-right-angle rotations', () {
      expect(() => toRgbImage(bgraGrid(), const FrameGeometry(rotation: 45)), throwsArgumentError);
    });
  });

  group('YUV420 conversion', () {
    RawFrame yuv(int w, int h, int yv, int u, int v, {int pixelStride = 1}) {
      final cw = w ~/ 2, ch = h ~/ 2;
      return RawFrame(
        width: w,
        height: h,
        format: RawFormat.yuv420,
        planes: [
          Uint8List(w * h)..fillRange(0, w * h, yv),
          Uint8List(cw * ch * pixelStride)..fillRange(0, cw * ch * pixelStride, u),
          Uint8List(cw * ch * pixelStride)..fillRange(0, cw * ch * pixelStride, v),
        ],
        rowStrides: [w, cw * pixelStride, cw * pixelStride],
        pixelStrides: [1, pixelStride, pixelStride],
      );
    }

    test('neutral chroma gives grey', () {
      final p = toRgbImage(yuv(8, 6, 120, 128, 128), const FrameGeometry(rotation: 0)).getPixel(3, 2);
      expect([p.r, p.g, p.b], [120, 120, 120]);
    });

    test('high V gives red, high U gives blue (BT.601), interleaved chroma stride', () {
      final red = toRgbImage(yuv(8, 6, 100, 128, 200, pixelStride: 2), const FrameGeometry(rotation: 0)).getPixel(0, 0);
      expect(red.r > red.g && red.r > red.b, isTrue);
      final blue = toRgbImage(yuv(8, 6, 100, 200, 128), const FrameGeometry(rotation: 0)).getPixel(0, 0);
      expect(blue.b > blue.r && blue.b > blue.g, isTrue);
    });

    test('portrait capture: 720x480 sensor frame rotated 270 and scaled to max 640', () {
      final out = toRgbImage(yuv(720, 480, 90, 128, 128), const FrameGeometry(rotation: 270));
      expect([out.width, out.height], [427, 640]);
      final jpeg = toJpeg(yuv(720, 480, 90, 128, 128), const FrameGeometry(rotation: 270));
      final decoded = img.decodeJpg(jpeg)!;
      expect([decoded.width, decoded.height], [427, 640]);
      expect(jpeg.length < 512 * 1024, isTrue); // server limit per frame
    });
  });

  test('evenlySample keeps order and count', () {
    expect(evenlySample(List.generate(10, (i) => i), 4), [0, 2, 5, 7]);
    expect(evenlySample([1, 2], 5), [1, 2]);
  });

  group('timeline (same schedule as the web client)', () {
    final t = buildTimeline(['turn_left', 'look_up'], 2.0);

    test('starts centred, each step followed by a return, then a final hold', () {
      expect(t.map((s) => s.kind).toList(), [
        SegmentKind.center,
        SegmentKind.turnLeft,
        SegmentKind.back,
        SegmentKind.lookUp,
        SegmentKind.back,
        SegmentKind.hold,
      ]);
      expect(t[1].start, closeTo(1.8, 1e-9));
      expect(t[1].duration, closeTo(1.2, 1e-9)); // round(2.0 * 0.62, 1)
      expect(t[2].duration, closeTo(0.8, 1e-9));
      expect(totalDuration(t), closeTo(5.8 + 1.2, 1e-9));
    });

    test('segmentAt finds the active prompt and returns null after the end', () {
      expect(segmentAt(t, 0)?.kind, SegmentKind.center);
      expect(segmentAt(t, 2.0)?.kind, SegmentKind.turnLeft);
      expect(segmentAt(t, 99), isNull);
    });

    test('passive liveness (no steps) is a steady frontal capture long enough for the server', () {
      final p = buildTimeline(const [], 2.6);
      expect(p.map((s) => s.kind).toList(), [SegmentKind.center, SegmentKind.hold]);
      expect(totalDuration(p), closeTo(4.2, 1e-9)); // >= 2 s server minimum, ~20 frames at 5 fps
    });

    test('unknown steps are rejected', () {
      expect(() => buildTimeline(['spin'], 2.0), throwsArgumentError);
    });
  });
}
