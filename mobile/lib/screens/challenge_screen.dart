// Guided face capture: camera preview, orientation calibration, timed prompts (a steady frontal look, or
// head movements when the server asks for them) and upload.
// The server decides; this screen only captures frames and shows the server's verdict.

import 'dart:async';

import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:screen_brightness/screen_brightness.dart';

import '../api.dart';
import '../app_state.dart';
import '../capture/frame_converter.dart';
import '../capture/timeline.dart';
import '../theme.dart';
import '../widgets/ui.dart';

class ChallengeSession {
  ChallengeSession(this.challenge, this.submit);

  final Map<String, dynamic> challenge;
  final Future<Map<String, dynamic>> Function(List<Uint8List> frames) submit;
}

enum _Phase { starting, preview, running, processing, submitting, result, error }

RawFrame _copy(CameraImage i) => RawFrame(
      width: i.width,
      height: i.height,
      format: i.format.group == ImageFormatGroup.bgra8888 ? RawFormat.bgra8888 : RawFormat.yuv420,
      planes: [for (final p in i.planes) Uint8List.fromList(p.bytes)],
      rowStrides: [for (final p in i.planes) p.bytesPerRow],
      pixelStrides: [for (final p in i.planes) p.bytesPerPixel ?? 1],
    );

// Top-level so compute() only sends the frames and geometry to the worker isolate, never widget state.
Uint8List _jpegTask((RawFrame, FrameGeometry) a) => toJpeg(a.$1, a.$2);
List<Uint8List> _jpegsTask((List<RawFrame>, FrameGeometry) a) => toJpegs(a.$1, a.$2);

bool _supported(CameraImage i) =>
    i.format.group == ImageFormatGroup.bgra8888 || (i.format.group == ImageFormatGroup.yuv420 && i.planes.length >= 3);

class ChallengeScreen extends StatefulWidget {
  const ChallengeScreen({
    super.key,
    required this.state,
    required this.title,
    required this.start,
    this.onAccepted,
    this.startLabel = 'Mulai verifikasi',
    this.debugPreview,
  });

  final AppState state;
  final String title;
  final String startLabel;
  final Future<ChallengeSession> Function() start;

  /// Runs after the server accepted the frames (e.g. records the check-in). May throw ApiException.
  final Future<String?> Function(Map<String, dynamic> result)? onAccepted;

  /// Tests only: show this widget instead of opening the camera (layout and screenshot tests).
  @visibleForTesting
  final Widget? debugPreview;

  @override
  State<ChallengeScreen> createState() => _ChallengeScreenState();
}

class _ChallengeScreenState extends State<ChallengeScreen> with WidgetsBindingObserver {
  CameraController? _cam;
  // Guards against opening the camera twice (e.g. the permission dialog pauses and resumes the app
  // while the first open is still in progress).
  bool _opening = false;
  _Phase _phase = _Phase.starting;
  String? _error;

  // Orientation calibration (rotation that makes the face upright, confirmed by the server's detector).
  late List<int> _candidates;
  int _candidate = 0;
  bool _calibrated = false;
  Timer? _qualityTimer;
  Completer<RawFrame>? _wantFrame;
  bool _checking = false;
  List<String> _hints = [];
  bool _faceOk = false;
  // Diagnostics shown on screen: what the camera delivers and what was last sent to the server.
  int _framesSeen = 0;
  String _lastFormat = '-';
  Uint8List? _lastSent;
  int? _lastFaces;

  // Running challenge.
  List<Segment> _timeline = [];
  Segment? _segment;
  double _progress = 0;
  final List<RawFrame> _frames = [];
  bool _capturing = false;
  DateTime _lastCapture = DateTime.fromMillisecondsSinceEpoch(0);
  Duration _captureInterval = const Duration(milliseconds: 125);
  Timer? _uiTimer;

  Map<String, dynamic>? _result;
  String? _successText;

  int get _rotation => _candidates[_candidate];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _brighten(true);
    if (widget.debugPreview != null) {
      _candidates = const [0];
      _calibrated = true;
      _faceOk = true;
      _phase = _Phase.preview;
    } else {
      _openCamera();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _stopTimers();
    _closeCamera();
    _brighten(false);
    super.dispose();
  }

  /// The white screen at full brightness works as a ring light for the face in dim places. Only this
  /// app's window brightness changes (no system setting); it is restored when the screen closes.
  Future<void> _brighten(bool on) async {
    try {
      if (on) {
        await ScreenBrightness.instance.setApplicationScreenBrightness(1.0);
      } else {
        await ScreenBrightness.instance.resetApplicationScreenBrightness();
      }
    } on Object catch (e) {
      debugPrint('screen brightness unavailable: $e');
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.inactive || s == AppLifecycleState.paused) {
      if (_phase == _Phase.preview && widget.debugPreview == null) {
        _stopTimers();
        _closeCamera();
        setState(() => _phase = _Phase.starting);
      }
    } else if (s == AppLifecycleState.resumed) {
      _brighten(true);
      if (_cam == null && _phase == _Phase.starting && widget.debugPreview == null) _openCamera();
    }
  }

  void _stopTimers() {
    _qualityTimer?.cancel();
    _uiTimer?.cancel();
  }

  Future<void> _closeCamera() async {
    final cam = _cam;
    _cam = null;
    if (cam == null) return;
    try {
      if (cam.value.isStreamingImages) await cam.stopImageStream();
    } on CameraException {
      // already stopped
    }
    await cam.dispose();
  }

  Future<void> _openCamera() async {
    if (_opening || _cam != null) return;
    _opening = true;
    setState(() {
      _phase = _Phase.starting;
      _error = null;
    });
    try {
      final cams = await availableCameras();
      final front = cams.firstWhere((c) => c.lensDirection == CameraLensDirection.front, orElse: () => cams.first);
      final initial = widget.state.cameraRotation ?? front.sensorOrientation;
      _candidates = [for (final d in [0, 180, 90, 270]) (initial + d) % 360];
      _candidate = 0;
      _calibrated = widget.state.cameraRotation != null;
      final cam = CameraController(
        front,
        ResolutionPreset.medium,
        enableAudio: false,
        imageFormatGroup: defaultTargetPlatform == TargetPlatform.iOS ? ImageFormatGroup.bgra8888 : ImageFormatGroup.yuv420,
      );
      await cam.initialize();
      if (!mounted) {
        await cam.dispose();
        return;
      }
      _cam = cam;
      await cam.startImageStream(_onImage);
      setState(() => _phase = _Phase.preview);
      _startQualityLoop();
    } on CameraException catch (e) {
      setState(() {
        _phase = _Phase.error;
        _error = e.code.contains('Access')
            ? 'Izin kamera ditolak. Izinkan kamera untuk aplikasi Absen di Pengaturan HP, lalu coba lagi.'
            : 'Kamera tidak dapat dibuka (${e.code}).';
      });
    } on StateError {
      setState(() {
        _phase = _Phase.error;
        _error = 'Tidak ada kamera yang ditemukan.';
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.error;
        _error = 'Kamera tidak dapat dimulai: $e';
      });
    } finally {
      _opening = false;
    }
  }

  void _onImage(CameraImage image) {
    _framesSeen++;
    _lastFormat = '${image.format.group.name}/${image.format.raw} ${image.width}x${image.height} '
        'planes=${image.planes.length}';
    if (!_supported(image)) return;
    final want = _wantFrame;
    if (want != null && !want.isCompleted) {
      _wantFrame = null;
      want.complete(_copy(image));
    }
    if (_capturing) {
      final now = DateTime.now();
      if (now.difference(_lastCapture) >= _captureInterval) {
        _lastCapture = now;
        _frames.add(_copy(image));
      }
    }
  }

  Future<RawFrame> _nextFrame() {
    final c = Completer<RawFrame>();
    _wantFrame = c;
    return c.future.timeout(const Duration(seconds: 3));
  }

  FrameGeometry get _geometry => FrameGeometry(rotation: _rotation, mirror: widget.state.mirrorFrames);

  // Advisory pre-check every 2.5 s (the server allows 30/min). Until a face is found, each miss tries the
  // next rotation, so the app adapts to however this phone's camera delivers its frames.
  void _startQualityLoop() {
    _qualityTimer?.cancel();
    _qualityTimer = Timer.periodic(const Duration(milliseconds: 2500), (_) => _qualityCheck());
    _qualityCheck();
  }

  Future<void> _qualityCheck() async {
    if (_checking || _phase != _Phase.preview) return;
    _checking = true;
    try {
      final raw = await _nextFrame();
      final jpeg = await compute(_jpegTask, (raw, _geometry));
      final res = await widget.state.api.postFrames('/biometrics/quality-check', [jpeg], field: 'frame')
          as Map<String, dynamic>;
      final faces = res['face_count'] as int? ?? 0;
      if (!mounted) return;
      setState(() {
        _lastSent = jpeg;
        _lastFaces = faces;
        _faceOk = res['ok'] == true;
        _hints = [for (final h in (res['hints'] as List? ?? const [])) h as String];
        if (faces > 0 && !_calibrated) {
          _calibrated = true;
          widget.state.setCameraRotation(_rotation);
        } else if (faces == 0 && !_calibrated) {
          _candidate = (_candidate + 1) % _candidates.length;
        }
      });
    } on ApiException catch (e) {
      if (mounted && e.status != 429) setState(() => _hints = [e.message]);
    } on TimeoutException {
      if (mounted) {
        setState(() => _hints = ['Kamera belum mengirim gambar yang bisa dipakai '
            '(diterima $_framesSeen frame, format $_lastFormat).']);
      }
    } on Object catch (e) {
      if (mounted) setState(() => _hints = ['Kesalahan: $e']);
    } finally {
      _checking = false;
    }
  }

  Future<void> _run() async {
    _qualityTimer?.cancel();
    setState(() {
      _error = null;
      _result = null;
    });
    final ChallengeSession session;
    try {
      session = await widget.start();
    } on ApiException catch (e) {
      setState(() => _error = e.message);
      _startQualityLoop();
      return;
    }
    final ch = session.challenge;
    final steps = [for (final s in ch['steps'] as List) s as String];
    _timeline = buildTimeline(steps, (ch['step_seconds'] as num).toDouble());
    final total = totalDuration(_timeline);
    final fps = (ch['recommended_fps'] as num).toDouble();
    // Stream frames arrive at irregular ~30-70 ms intervals; aiming 10 % early keeps the real rate close to
    // the server's recommendation (it was ~4 fps instead of 5 on a Samsung A56).
    _captureInterval = Duration(microseconds: (0.9e6 / fps).round());
    final maxFrames = ch['max_frames'] as int;
    final t0 = DateTime.now();
    _frames.clear();
    _lastCapture = DateTime.fromMillisecondsSinceEpoch(0);
    setState(() {
      _phase = _Phase.running;
      _segment = _timeline.first;
      _progress = 0;
    });
    _capturing = true;
    final done = Completer<void>();
    _uiTimer = Timer.periodic(const Duration(milliseconds: 100), (t) {
      final elapsed = DateTime.now().difference(t0).inMilliseconds / 1000;
      if (elapsed >= total) {
        t.cancel();
        _capturing = false;
        if (!done.isCompleted) done.complete();
      }
      if (mounted) {
        setState(() {
          _progress = (elapsed / total).clamp(0, 1);
          _segment = segmentAt(_timeline, elapsed);
        });
      }
    });
    await done.future;

    setState(() => _phase = _Phase.processing);
    final raw = evenlySample(List<RawFrame>.of(_frames), maxFrames);
    _frames.clear();
    final jpegs = await compute(_jpegsTask, (raw, _geometry));

    setState(() => _phase = _Phase.submitting);
    try {
      final result = await session.submit(jpegs);
      String? success;
      if (result['accepted'] == true && widget.onAccepted != null) {
        success = await widget.onAccepted!(result);
      }
      if (result['accepted'] == true) await _closeCamera();
      setState(() {
        _result = result;
        _successText = success;
        _phase = _Phase.result;
      });
    } on ApiException catch (e) {
      setState(() {
        _error = e.message;
        _phase = _Phase.result;
      });
    }
  }

  void _retry() {
    setState(() {
      _result = null;
      _error = null;
    });
    if (_cam == null && widget.debugPreview == null) {
      _openCamera();
    } else {
      setState(() => _phase = _Phase.preview);
      _startQualityLoop();
    }
  }

  // --- UI ------------------------------------------------------------------------------------------------
  // Always light and white: at full brightness the screen lights the face like a ring light.

  @override
  Widget build(BuildContext context) => Theme(
        data: buildTheme(Brightness.light),
        child: Builder(builder: _page),
      );

  Widget _page(BuildContext context) {
    final accepted = _phase == _Phase.result && _result?['accepted'] == true;
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: Colors.white,
        foregroundColor: AppColors.ink,
        title: Text(widget.title),
      ),
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, box) => SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(24, 4, 24, 24),
            child: ConstrainedBox(
              constraints: BoxConstraints(minHeight: box.maxHeight - 28),
              child: AnimatedSwitcher(
                duration: const Duration(milliseconds: 300),
                child: accepted ? _success(context) : _capture(context, box.maxWidth),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Color get _ringColor => switch (_phase) {
        _Phase.preview => _calibrated && _faceOk ? AppColors.success : AppColors.brand,
        _Phase.running || _Phase.processing || _Phase.submitting => AppColors.brand,
        _Phase.result => AppColors.danger,
        _Phase.error => AppColors.danger,
        _Phase.starting => AppColors.line,
      };

  Widget _capture(BuildContext context, double width) {
    final theme = Theme.of(context);
    final size = (width - 48).clamp(220.0, 340.0);
    final busy = _phase == _Phase.starting || _phase == _Phase.processing || _phase == _Phase.submitting;
    final failed = _phase == _Phase.result || _phase == _Phase.error;
    return Column(
      key: const ValueKey('capture'),
      children: [
        if (_phase == _Phase.starting || _phase == _Phase.preview || _phase == _Phase.running)
          const StatusPill(label: 'Layar terang untuk menerangi wajah', tone: Tone.brand, icon: Icons.light_mode_rounded),
        const SizedBox(height: 8),
        _FaceRing(
          size: size,
          color: _ringColor,
          progress: _phase == _Phase.running ? _progress : null,
          child: Stack(
            fit: StackFit.expand,
            children: [
              _cameraView(),
              if (busy)
                ColoredBox(
                  color: Colors.white.withValues(alpha: _phase == _Phase.starting ? 0.0 : 0.55),
                  child: const Center(child: CircularProgressIndicator(strokeWidth: 3)),
                ),
            ],
          ),
        ),
        const SizedBox(height: 28),
        if (_phase == _Phase.running && _segment != null) ...[
          Icon(_promptIcon(_segment!.kind), size: 34, color: AppColors.brand),
          const SizedBox(height: 8),
        ],
        Text(
          _headline,
          textAlign: TextAlign.center,
          style: theme.textTheme.headlineSmall?.copyWith(color: failed ? AppColors.danger : AppColors.ink),
        ),
        const SizedBox(height: 8),
        Text(
          _subline,
          textAlign: TextAlign.center,
          style: theme.textTheme.bodyMedium?.copyWith(color: AppColors.muted, height: 1.4),
        ),
        const SizedBox(height: 14),
        Wrap(
          alignment: WrapAlignment.center,
          spacing: 6,
          runSpacing: 6,
          children: [
            for (final h in _visibleHints) StatusPill(label: h, tone: Tone.warning, icon: Icons.lightbulb_outline_rounded),
          ],
        ),
        if (_error != null && _phase != _Phase.error && _phase != _Phase.result) ...[
          const SizedBox(height: 12),
          MessageBox(text: _error!),
        ],
        const SizedBox(height: 28),
        ..._actions(),
        if (_phase == _Phase.preview || _phase == _Phase.result) ...[
          const SizedBox(height: 12),
          _diagnostics(theme),
        ],
      ],
    );
  }

  Widget _cameraView() {
    final preview = widget.debugPreview;
    if (preview != null) return preview;
    final cam = _cam;
    if (cam == null || !cam.value.isInitialized) {
      return const ColoredBox(
        color: Color(0xFFF1F4FA),
        child: Center(child: Icon(Icons.face_rounded, size: 96, color: Color(0xFFCBD5E1))),
      );
    }
    return FittedBox(
      fit: BoxFit.cover,
      child: SizedBox(
        width: cam.value.previewSize?.height ?? 300,
        height: cam.value.previewSize?.width ?? 400,
        child: CameraPreview(cam),
      ),
    );
  }

  String get _headline => switch (_phase) {
        _Phase.starting => 'Menyiapkan kamera…',
        _Phase.preview => !_calibrated
            ? 'Mencari wajah…'
            : (_faceOk ? 'Wajah terdeteksi' : 'Posisikan wajah di dalam lingkaran'),
        _Phase.running => _segment?.label ?? 'Tahan…',
        _Phase.processing => 'Memproses gambar…',
        _Phase.submitting => 'Memverifikasi wajah…',
        _Phase.error => 'Kamera tidak bisa dibuka',
        _Phase.result => 'Verifikasi gagal',
      };

  String get _subline => switch (_phase) {
        _Phase.starting => 'Layar dibuat terang untuk menerangi wajah Anda.',
        _Phase.preview => _calibrated && _faceOk
            ? 'Tekan tombol di bawah, lalu ikuti petunjuk di layar. Gerakkan kepala perlahan, HP tetap diam.'
            : 'Hadapkan wajah ke kamera dan pastikan cahaya cukup. Layar yang terang membantu menerangi wajah.',
        _Phase.running => switch (_segment?.kind) {
            SegmentKind.turnLeft || SegmentKind.turnRight => 'Putar kepalanya saja, HP tetap diam.',
            SegmentKind.back => 'Hadapkan wajah lurus ke kamera lagi.',
            _ => 'Jangan gerakkan HP.',
          },
        _Phase.processing || _Phase.submitting => 'Mohon tunggu sebentar.',
        _Phase.error => _error ?? 'Terjadi masalah pada kamera.',
        _Phase.result => _error ?? (_result?['message'] as String? ?? 'Silakan coba lagi.'),
      };

  List<String> get _visibleHints {
    if (_phase == _Phase.result) return [for (final h in (_result?['hints'] as List? ?? const [])) h as String];
    if (_phase == _Phase.preview) return _hints;
    return const [];
  }

  static IconData _promptIcon(SegmentKind kind) => switch (kind) {
        SegmentKind.turnLeft => Icons.arrow_back_rounded,
        SegmentKind.turnRight => Icons.arrow_forward_rounded,
        SegmentKind.lookUp => Icons.arrow_upward_rounded,
        SegmentKind.lookDown => Icons.arrow_downward_rounded,
        SegmentKind.hold => Icons.pan_tool_alt_rounded,
        _ => Icons.center_focus_strong_rounded,
      };

  List<Widget> _actions() => switch (_phase) {
        _Phase.preview => [
            GradientButton(
              label: widget.startLabel,
              icon: Icons.play_arrow_rounded,
              onPressed: _calibrated ? _run : null,
            ),
          ],
        _Phase.error => [
            GradientButton(label: 'Coba lagi', icon: Icons.refresh_rounded, onPressed: _openCamera),
          ],
        _Phase.result => [
            GradientButton(label: 'Coba lagi', icon: Icons.refresh_rounded, onPressed: _retry),
            const SizedBox(height: 8),
            TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Kembali')),
          ],
        _ => const [],
      };

  Widget _success(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      key: const ValueKey('success'),
      children: [
        const SizedBox(height: 40),
        TweenAnimationBuilder<double>(
          tween: Tween(begin: 0.6, end: 1),
          duration: const Duration(milliseconds: 500),
          curve: Curves.elasticOut,
          builder: (context, v, child) => Transform.scale(scale: v, child: child),
          child: Container(
            width: 140,
            height: 140,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: AppColors.successGradient,
              boxShadow: [BoxShadow(color: AppColors.success.withValues(alpha: 0.35), blurRadius: 40, offset: const Offset(0, 16))],
            ),
            child: const Icon(Icons.check_rounded, color: Colors.white, size: 84),
          ),
        ),
        const SizedBox(height: 32),
        Text('Berhasil!', style: theme.textTheme.headlineMedium),
        const SizedBox(height: 10),
        Text(
          _successText ?? 'Verifikasi wajah berhasil.',
          textAlign: TextAlign.center,
          style: theme.textTheme.titleMedium?.copyWith(color: AppColors.muted, fontWeight: FontWeight.w600, height: 1.45),
        ),
        const SizedBox(height: 40),
        GradientButton(
          label: 'Selesai',
          icon: Icons.done_all_rounded,
          gradient: AppColors.successGradient,
          onPressed: () => Navigator.of(context).pop(true),
        ),
      ],
    );
  }

  /// What the server actually receives, so capture problems can be diagnosed from a screenshot.
  Widget _diagnostics(ThemeData theme) {
    final sent = _lastSent;
    return Theme(
      data: theme.copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        title: Text('Info teknis', style: theme.textTheme.bodySmall?.copyWith(color: AppColors.muted)),
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(10),
                child: Container(
                  width: 60,
                  height: 80,
                  color: const Color(0xFFF1F4FA),
                  child: sent == null
                      ? const Icon(Icons.image_not_supported_outlined, color: AppColors.muted)
                      : Image.memory(sent, fit: BoxFit.cover),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  'Gambar terakhir ke server${_lastFaces == null ? '' : ' · wajah: $_lastFaces'}\n'
                  'Rotasi $_rotation°${_calibrated ? ' (terkunci)' : ''} · cermin '
                  '${widget.state.mirrorFrames ? 'ya' : 'tidak'}\n'
                  'Kamera $_lastFormat · $_framesSeen frame',
                  style: theme.textTheme.bodySmall?.copyWith(color: AppColors.muted, height: 1.4),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// Circular camera window with a coloured ring; while recording the ring fills up as progress.
class _FaceRing extends StatelessWidget {
  const _FaceRing({required this.size, required this.color, required this.child, this.progress});

  final double size;
  final Color color;
  final double? progress;
  final Widget child;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: size + 28,
        height: size + 28,
        child: Stack(
          alignment: Alignment.center,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 300),
              width: size + 12,
              height: size + 12,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                boxShadow: [BoxShadow(color: color.withValues(alpha: 0.28), blurRadius: 36, spreadRadius: 2)],
              ),
            ),
            SizedBox(
              width: size + 20,
              height: size + 20,
              child: CustomPaint(painter: _RingPainter(color: color, progress: progress)),
            ),
            ClipOval(child: SizedBox(width: size, height: size, child: child)),
          ],
        ),
      );
}

class _RingPainter extends CustomPainter {
  const _RingPainter({required this.color, this.progress});

  final Color color;
  final double? progress;

  @override
  void paint(Canvas canvas, Size size) {
    const stroke = 6.0;
    final rect = (Offset.zero & size).deflate(stroke / 2);
    final track = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..color = progress == null ? color : const Color(0xFFE2E8F0);
    canvas.drawArc(rect, 0, 6.283185307179586, false, track);
    final p = progress;
    if (p != null) {
      final arc = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = stroke
        ..strokeCap = StrokeCap.round
        ..shader = const SweepGradient(
          colors: [Color(0xFF2563EB), Color(0xFF4338CA), Color(0xFF22C55E)],
          transform: GradientRotation(-1.5707963267948966),
        ).createShader(rect);
      canvas.drawArc(rect, -1.5707963267948966, 6.283185307179586 * p.clamp(0.0, 1.0), false, arc);
    }
  }

  @override
  bool shouldRepaint(covariant _RingPainter old) => old.color != color || old.progress != progress;
}
