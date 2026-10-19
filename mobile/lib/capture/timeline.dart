// The guided prompt schedule — same timing as the web client (frontend/src/lib/capture.ts).

enum SegmentKind { center, turnLeft, turnRight, lookUp, lookDown, back, hold }

SegmentKind stepKind(String step) => switch (step) {
      'turn_left' => SegmentKind.turnLeft,
      'turn_right' => SegmentKind.turnRight,
      'look_up' => SegmentKind.lookUp,
      'look_down' => SegmentKind.lookDown,
      _ => throw ArgumentError.value(step, 'step', 'unknown challenge step'),
    };

const stepText = {
  SegmentKind.center: 'Lihat lurus ke kamera',
  SegmentKind.turnLeft: 'Putar kepala perlahan ke kiri Anda',
  SegmentKind.turnRight: 'Putar kepala perlahan ke kanan Anda',
  SegmentKind.lookUp: 'Dongakkan kepala ke atas',
  SegmentKind.lookDown: 'Tundukkan kepala ke bawah',
  SegmentKind.back: 'Kembali menghadap tengah',
  SegmentKind.hold: 'Tahan, tetap lihat ke kamera',
};

class Segment {
  const Segment(this.kind, this.start, this.duration);

  final SegmentKind kind;
  final double start; // seconds from challenge start
  final double duration;

  String get label => stepText[kind]!;
}

double _round1(double v) => (v * 10).round() / 10;

/// Start centred, then each step followed by a return to centre, then a short final hold so the last
/// return is still captured when the user reacts late (~0.5-0.7 s measured on real captures).
List<Segment> buildTimeline(
  List<String> steps,
  double stepSeconds, {
  double initialCenter = 1.8,
  double finalHold = 1.2,
  double passiveSeconds = 3.0,
}) {
  if (steps.isEmpty) {
    // Passive liveness (server sends no movement steps): a steady frontal capture.
    return [Segment(SegmentKind.center, 0, passiveSeconds), Segment(SegmentKind.hold, passiveSeconds, finalHold)];
  }
  final hold = _round1(stepSeconds * 0.62);
  final back = _round1(stepSeconds - hold);
  final segments = <Segment>[Segment(SegmentKind.center, 0, initialCenter)];
  var t = initialCenter;
  for (final step in steps) {
    segments.add(Segment(stepKind(step), t, hold));
    t += hold;
    segments.add(Segment(SegmentKind.back, t, back));
    t += back;
  }
  segments.add(Segment(SegmentKind.hold, t, finalHold));
  return segments;
}

double totalDuration(List<Segment> timeline) => timeline.isEmpty ? 0 : timeline.last.start + timeline.last.duration;

Segment? segmentAt(List<Segment> timeline, double elapsed) {
  for (final s in timeline) {
    if (elapsed >= s.start && elapsed < s.start + s.duration) return s;
  }
  return null;
}
