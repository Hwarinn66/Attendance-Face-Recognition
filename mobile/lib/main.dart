import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:intl/intl.dart';

import 'app_state.dart';
import 'screens/change_password_screen.dart';
import 'screens/home_screen.dart';
import 'screens/login_screen.dart';
import 'theme.dart';
import 'widgets/ui.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  // Show the UI immediately; start-up work happens in [BootScreen] so a failure is visible, never a frozen splash.
  runApp(const AbsenApp());
}

Future<AppState> boot() async {
  // Portrait only: the capture pipeline assumes the phone is held upright.
  await SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]);
  try {
    await initializeDateFormatting('id_ID');
    Intl.defaultLocale = 'id_ID';
  } on Object catch (e) {
    debugPrint('Indonesian date format unavailable, using default: $e');
  }
  final state = await AppState.load();
  // Restoring a saved session needs the server; never let an unreachable server block start-up.
  try {
    await state.restore().timeout(const Duration(seconds: 8));
  } on TimeoutException {
    debugPrint('session restore timed out; showing sign-in');
  }
  return state;
}

class AbsenApp extends StatelessWidget {
  const AbsenApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Absen',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(Brightness.light),
        darkTheme: buildTheme(Brightness.dark),
        home: const BootScreen(),
      );
}

class BootScreen extends StatefulWidget {
  const BootScreen({super.key});

  @override
  State<BootScreen> createState() => _BootScreenState();
}

class _BootScreenState extends State<BootScreen> {
  late Future<AppState> _boot = boot();

  @override
  Widget build(BuildContext context) => FutureBuilder<AppState>(
        future: _boot,
        builder: (context, snap) {
          if (snap.hasData) return Root(state: snap.data!);
          return Scaffold(
            body: Container(
              width: double.infinity,
              decoration: const BoxDecoration(gradient: AppColors.brandGradient),
              padding: const EdgeInsets.all(28),
              child: SafeArea(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const BrandMark(size: 88),
                    const SizedBox(height: 20),
                    const Text('Absen', style: TextStyle(color: Colors.white, fontSize: 32, fontWeight: FontWeight.w900)),
                    const SizedBox(height: 28),
                    if (snap.hasError) ...[
                      const Text('Aplikasi gagal dimulai.',
                          style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 8),
                      SelectableText('${snap.error}',
                          textAlign: TextAlign.center, style: TextStyle(color: Colors.white.withValues(alpha: 0.85))),
                      const SizedBox(height: 16),
                      FilledButton(
                        style: FilledButton.styleFrom(backgroundColor: Colors.white, foregroundColor: AppColors.brand),
                        onPressed: () => setState(() => _boot = boot()),
                        child: const Text('Coba lagi'),
                      ),
                    ] else
                      const SizedBox(
                        width: 28,
                        height: 28,
                        child: CircularProgressIndicator(strokeWidth: 3, color: Colors.white),
                      ),
                  ],
                ),
              ),
            ),
          );
        },
      );
}

class Root extends StatelessWidget {
  const Root({super.key, required this.state});

  final AppState state;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: state,
        builder: (context, _) {
          if (!state.signedIn) return LoginScreen(state: state);
          if (state.me?['must_change_password'] == true) return ChangePasswordScreen(state: state);
          return HomeScreen(key: ValueKey(state.me?['id']), state: state);
        },
      );
}
