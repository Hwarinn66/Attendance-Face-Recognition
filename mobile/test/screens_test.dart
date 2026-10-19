// Renders every screen against a mock backend. Always checks that each screen builds without errors;
// with SCREENSHOT_DIR set it also writes PNG screenshots (real Roboto + Material Icons fonts) for review.

import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:absen_mobile/app_state.dart';
import 'package:absen_mobile/screens/challenge_screen.dart';
import 'package:absen_mobile/screens/enroll_screen.dart';
import 'package:absen_mobile/screens/history_screen.dart';
import 'package:absen_mobile/screens/home_screen.dart';
import 'package:absen_mobile/screens/login_screen.dart';
import 'package:absen_mobile/screens/settings_screen.dart';
import 'package:absen_mobile/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:intl/intl.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _shotKey = Key('screenshot');

Future<void> _loadFonts() async {
  // flutter_tester runs from <flutter>/bin/cache/artifacts/engine/<platform>/; fonts ship in artifacts/material_fonts.
  Directory? dir = File(Platform.resolvedExecutable).parent;
  Directory? fonts;
  for (var i = 0; i < 5 && dir != null; i++) {
    final candidate = Directory('${dir.path}${Platform.pathSeparator}material_fonts');
    if (candidate.existsSync()) {
      fonts = candidate;
      break;
    }
    dir = dir.parent;
  }
  if (fonts == null) return; // boxes instead of glyphs; the build checks still run
  Future<void> load(String family, List<String> files) async {
    final loader = FontLoader(family);
    for (final f in files) {
      final file = File('${fonts!.path}${Platform.pathSeparator}$f');
      if (file.existsSync()) loader.addFont(Future.value(ByteData.sublistView(file.readAsBytesSync())));
    }
    await loader.load();
  }

  await load('Roboto', ['roboto-regular.ttf', 'roboto-medium.ttf', 'roboto-bold.ttf', 'roboto-black.ttf']);
  await load('MaterialIcons', ['materialicons-regular.otf']);
}

String _iso(DateTime t) => t.toUtc().toIso8601String();

/// Fake backend with one employee, today's shift and a few records.
http.Client _backend({required bool checkedIn, required bool consentNeeded, required String bio}) {
  final now = DateTime.now();
  final day = DateTime(now.year, now.month, now.day);
  final start = now.subtract(const Duration(minutes: 20));
  final date = '${day.year}-${day.month.toString().padLeft(2, '0')}-${day.day.toString().padLeft(2, '0')}';
  Map<String, dynamic> record(int daysAgo, {int late = 0, bool out = true}) {
    final d = day.subtract(Duration(days: daysAgo));
    final inAt = d.add(Duration(hours: 8, minutes: late));
    return {
      'id': 'r$daysAgo',
      'shift_name': 'Office hours',
      'shift_date': '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}',
      'check_in_at': _iso(inAt),
      'check_out_at': out ? _iso(d.add(const Duration(hours: 17, minutes: 4))) : null,
      'check_out_status': out ? 'on_time' : null,
      'late_minutes': late,
      'worked_minutes': out ? 480 - late : null,
      'is_corrected': false,
    };
  }

  final open = checkedIn
      ? {...record(0, out: false), 'id': 'today', 'shift_name': 'Uji coba (setiap hari)', 'check_in_at': _iso(start.add(const Duration(minutes: 3)))}
      : null;
  final occurrence = {
    'shift_id': 's1',
    'shift_name': 'Uji coba (setiap hari)',
    'shift_date': date,
    'start_at': _iso(start),
    'end_at': _iso(start.add(const Duration(hours: 2))),
    'check_in_opens_at': _iso(start.subtract(const Duration(hours: 2))),
    'check_in_closes_at': _iso(start.add(const Duration(minutes: 30))),
    'check_out_opens_at': _iso(start.add(const Duration(minutes: 35))),
    'check_out_closes_at': _iso(start.add(const Duration(hours: 7))),
    'record_id': checkedIn ? 'today' : null,
  };
  final me = {
    'id': 'u1',
    'email': 'admin+employee@example.com',
    'username': 'employee123',
    'full_name': 'Dewi Lestari',
    'role': 'employee',
    'must_change_password': false,
    'permissions': ['self_service'],
  };
  return MockClient((req) async {
    final path = req.url.path.replaceFirst('/api/v1', '');
    final Object body = switch (path) {
      '/auth/token' => {'access_token': 't', 'token_type': 'bearer', 'expires_in': 3600, 'user': me},
      '/auth/me' => me,
      '/attendance/status' => {
          'server_time': _iso(now),
          'biometric_status': bio,
          'can_check_in': !checkedIn,
          'can_check_out': false,
          'check_in_blocker': checkedIn ? 'already_checked_in' : null,
          'check_out_blocker': checkedIn ? 'check_out_not_open_yet' : null,
          'open_record': open,
          'today': [occurrence],
          'next_occurrence': null,
          'geofence_required': false,
        },
      '/attendance/me' => {
          'items': [?open, record(1), record(2, late: 12), record(3), record(4, late: 3)],
          'total': 5,
        },
      '/biometrics/me' => {
          'status': bio,
          'consent_current': !consentNeeded,
          'can_start_enrollment': true,
          'attempts_remaining_today': 17,
          'verification_available': true,
          'pad_enforced': true,
        },
      '/biometrics/consent-text' => {
          'version': '2026-10-v1',
          'text': 'Dengan menyetujui, Anda mengizinkan perusahaan menyimpan templat wajah terenkripsi untuk '
              'verifikasi absensi. Gambar wajah tidak disimpan. Anda dapat menarik persetujuan dan menghapus '
              'data wajah kapan saja melalui administrator.',
        },
      '/health/ready' => {'status': 'ok', 'checks': {'verification': 'available'}},
      _ => {'error': {'code': 'not_found', 'message': 'not found'}},
    };
    return http.Response(jsonEncode(body), 200, headers: {'content-type': 'application/json'});
  });
}

Future<AppState> _state(WidgetTester tester, http.Client client, {bool signedIn = true}) async {
  final state = (await tester.runAsync(() async {
    final s = await AppState.load(client: client);
    if (signedIn) await s.signIn('Employee123', 'Employee');
    return s;
  }))!;
  return state;
}

Future<void> _show(WidgetTester tester, Widget screen, String name, {Brightness brightness = Brightness.light}) async {
  tester.view.physicalSize = const Size(1170, 2532);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    RepaintBoundary(
      key: _shotKey,
      child: MaterialApp(debugShowCheckedModeBanner: false, theme: buildTheme(brightness), home: screen),
    ),
  );
  // Let the mock HTTP calls complete and the UI settle.
  for (var i = 0; i < 5; i++) {
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 30)));
    await tester.pump(const Duration(milliseconds: 100));
  }
  await tester.pump(const Duration(seconds: 1));
  expect(tester.takeException(), isNull);
  final dir = Platform.environment['SCREENSHOT_DIR'];
  if (dir != null) {
    final boundary = tester.renderObject<RenderRepaintBoundary>(find.byKey(_shotKey));
    await tester.runAsync(() async {
      final image = await boundary.toImage(pixelRatio: 1.5);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      File('$dir${Platform.pathSeparator}$name.png').writeAsBytesSync(bytes!.buffer.asUint8List());
    });
  }
}

void main() {
  setUpAll(() async {
    TestWidgetsFlutterBinding.ensureInitialized();
    await _loadFonts();
    await initializeDateFormatting('id_ID');
    Intl.defaultLocale = 'id_ID';
  });
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('login', (tester) async {
    final state = await _state(tester, _backend(checkedIn: false, consentNeeded: false, bio: 'active'), signedIn: false);
    await _show(tester, LoginScreen(state: state), '01_login');
    expect(find.text('Masuk'), findsWidgets);
  });

  testWidgets('home, check-in open', (tester) async {
    final state = await _state(tester, _backend(checkedIn: false, consentNeeded: false, bio: 'active'));
    await _show(tester, HomeScreen(state: state), '02_home_checkin');
    expect(find.text('Check-in sekarang'), findsOneWidget);
  });

  testWidgets('home, checked in', (tester) async {
    final state = await _state(tester, _backend(checkedIn: true, consentNeeded: false, bio: 'active'));
    await _show(tester, HomeScreen(state: state), '03_home_checked_in');
    expect(find.text('Sudah check-in'), findsOneWidget);
  });

  testWidgets('home, dark mode', (tester) async {
    final state = await _state(tester, _backend(checkedIn: false, consentNeeded: false, bio: 'active'));
    await _show(tester, HomeScreen(state: state), '04_home_dark', brightness: Brightness.dark);
  });

  testWidgets('enroll, consent', (tester) async {
    final state = await _state(tester, _backend(checkedIn: false, consentNeeded: true, bio: 'not_enrolled'));
    await _show(tester, EnrollScreen(state: state), '05_enroll_consent');
    expect(find.text('Setuju & lanjutkan'), findsOneWidget);
  });

  testWidgets('enroll, ready', (tester) async {
    final state = await _state(tester, _backend(checkedIn: false, consentNeeded: false, bio: 'pending_retry'));
    await _show(tester, EnrollScreen(state: state), '06_enroll_ready');
    expect(find.text('Daftarkan wajah'), findsWidgets);
  });

  testWidgets('capture screen is white and shows the camera in a ring', (tester) async {
    final state = await _state(tester, _backend(checkedIn: false, consentNeeded: false, bio: 'active'));
    final preview = Container(
      decoration: const BoxDecoration(
        gradient: LinearGradient(colors: [Color(0xFFCBB59E), Color(0xFF8D6E58)], begin: Alignment.topCenter, end: Alignment.bottomCenter),
      ),
      child: const Icon(Icons.face, size: 220, color: Color(0x66FFFFFF)),
    );
    await _show(
      tester,
      ChallengeScreen(
        state: state,
        title: 'Check-in',
        startLabel: 'Mulai check-in',
        start: () async => throw UnimplementedError(),
        debugPreview: preview,
      ),
      '07_capture_ready',
      brightness: Brightness.dark, // the capture screen must stay white even in dark mode
    );
    expect(find.text('Wajah terdeteksi'), findsOneWidget);
    final scaffold = tester.widget<Scaffold>(find.byType(Scaffold).first);
    expect(scaffold.backgroundColor, Colors.white);
  });

  testWidgets('history', (tester) async {
    final state = await _state(tester, _backend(checkedIn: true, consentNeeded: false, bio: 'active'));
    await _show(tester, HistoryScreen(state: state), '08_history');
    expect(find.text('Terlambat 12 mnt'), findsOneWidget);
  });

  testWidgets('settings', (tester) async {
    final state = await _state(tester, _backend(checkedIn: false, consentNeeded: false, bio: 'active'));
    await _show(tester, SettingsScreen(state: state), '09_settings');
  });
}
