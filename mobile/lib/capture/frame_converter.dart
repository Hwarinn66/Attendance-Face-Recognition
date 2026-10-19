// Converts raw camera stream frames into upright JPEGs for upload.
//
// The phone only captures: every liveness and identity decision is made by the server. Frames are sent
// un-mirrored (as the camera sees the user), like the web client, so "turn left/right" means the same
// thing on both clients.

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:image/image.dart' as img;

enum RawFormat { yuv420, bgra8888 }

/// A copy of one camera stream frame (plane buffers are reused by the plugin, so they must be copied).
class RawFrame {
  RawFrame({
    required this.width,
    required this.height,
    required this.format,
    required this.planes,
    required this.rowStrides,
    required this.pixelStrides,
  });

  final int width;
  final int height;
  final RawFormat format;
  final List<Uint8List> planes;
  final List<int> rowStrides;
  final List<int> pixelStrides;
}

/// Output geometry: rotate clockwise by [rotation] degrees, optionally mirror, then scale so the longer
/// side is at most [maxSide] pixels (never upscaled).
class FrameGeometry {
  const FrameGeometry({required this.rotation, this.mirror = false, this.maxSide = 640, this.quality = 85});

  final int rotation;
  final bool mirror;
  final int maxSide;
  final int quality;
}

int _clamp255(double v) => v < 0 ? 0 : (v > 255 ? 255 : v.round());

/// Upright RGB image for [frame] under [geo]. Single pass: each output pixel samples its source pixel.
img.Image toRgbImage(RawFrame frame, FrameGeometry geo) {
  final rot = ((geo.rotation % 360) + 360) % 360;
  if (rot % 90 != 0) throw ArgumentError.value(geo.rotation, 'rotation', 'must be a multiple of 90');
  final w = frame.width, h = frame.height;
  final rotW = rot % 180 == 0 ? w : h;
  final rotH = rot % 180 == 0 ? h : w;
  final scale = math.min(1.0, geo.maxSide / math.max(rotW, rotH));
  final outW = math.max(1, (rotW * scale).round());
  final outH = math.max(1, (rotH * scale).round());
  final out = Uint8List(outW * outH * 3);

  var o = 0;
  for (var y = 0; y < outH; y++) {
    final ry = math.min(rotH - 1, (y / scale).floor());
    for (var x = 0; x < outW; x++) {
      var rx = math.min(rotW - 1, (x / scale).floor());
      if (geo.mirror) rx = rotW - 1 - rx;
      // Inverse of a clockwise rotation: rotated (rx, ry) -> source (sx, sy).
      final int sx, sy;
      switch (rot) {
        case 90:
          sx = ry;
          sy = h - 1 - rx;
        case 180:
          sx = w - 1 - rx;
          sy = h - 1 - ry;
        case 270:
          sx = w - 1 - ry;
          sy = rx;
        default:
          sx = rx;
          sy = ry;
      }
      if (frame.format == RawFormat.bgra8888) {
        final i = sy * frame.rowStrides[0] + sx * 4;
        final p = frame.planes[0];
        out[o++] = p[i + 2];
        out[o++] = p[i + 1];
        out[o++] = p[i];
      } else {
        final yv = frame.planes[0][sy * frame.rowStrides[0] + sx * frame.pixelStrides[0]].toDouble();
        final ci1 = (sy >> 1) * frame.rowStrides[1] + (sx >> 1) * frame.pixelStrides[1];
        final ci2 = (sy >> 1) * frame.rowStrides[2] + (sx >> 1) * frame.pixelStrides[2];
        final u = frame.planes[1][ci1] - 128.0;
        final v = frame.planes[2][ci2] - 128.0;
        // BT.601 full range (Android camera YUV_420_888).
        out[o++] = _clamp255(yv + 1.402 * v);
        out[o++] = _clamp255(yv - 0.344136 * u - 0.714136 * v);
        out[o++] = _clamp255(yv + 1.772 * u);
      }
    }
  }
  return img.Image.fromBytes(width: outW, height: outH, bytes: out.buffer, numChannels: 3);
}

Uint8List toJpeg(RawFrame frame, FrameGeometry geo) =>
    img.encodeJpg(toRgbImage(frame, geo), quality: geo.quality);

/// Converts a whole capture (run inside an isolate).
List<Uint8List> toJpegs(List<RawFrame> frames, FrameGeometry geo) => [for (final f in frames) toJpeg(f, geo)];

/// Picks [n] items evenly spread over [items] (keeps order).
List<T> evenlySample<T>(List<T> items, int n) {
  if (items.length <= n) return items;
  return [for (var i = 0; i < n; i++) items[(i * items.length / n).floor()]];
}
