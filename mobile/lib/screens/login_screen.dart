import 'package:flutter/material.dart';

import '../api.dart';
import '../app_state.dart';
import '../theme.dart';
import '../widgets/ui.dart';
import 'settings_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.state});

  final AppState state;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _login = TextEditingController();
  final _password = TextEditingController();
  bool _busy = false;
  bool _obscure = true;
  String? _error;

  @override
  void dispose() {
    _login.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_login.text.trim().isEmpty || _password.text.isEmpty) {
      setState(() => _error = 'Isi ID/email dan password.');
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.state.signIn(_login.text.trim(), _password.text);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final top = MediaQuery.paddingOf(context).top;
    return Scaffold(
      body: SingleChildScrollView(
        child: Column(
          children: [
            // Hero header ---------------------------------------------------------------------------------
            Container(
              width: double.infinity,
              padding: EdgeInsets.fromLTRB(24, top + 12, 12, 72),
              decoration: const BoxDecoration(
                gradient: AppColors.brandGradient,
                borderRadius: BorderRadius.vertical(bottom: Radius.circular(36)),
              ),
              child: Stack(
                children: [
                  const Positioned(right: -30, top: 30, child: _Glow(size: 160)),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Align(
                        alignment: Alignment.centerRight,
                        child: IconButton(
                          tooltip: 'Pengaturan server',
                          color: Colors.white,
                          icon: const Icon(Icons.tune_rounded),
                          onPressed: () => Navigator.of(context).push(
                            MaterialPageRoute<void>(builder: (_) => SettingsScreen(state: widget.state)),
                          ),
                        ),
                      ),
                      const SizedBox(height: 8),
                      const BrandMark(size: 68),
                      const SizedBox(height: 20),
                      const Text(
                        'Absen',
                        style: TextStyle(color: Colors.white, fontSize: 34, fontWeight: FontWeight.w900, letterSpacing: -1),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        'Absensi cepat dan aman dengan\nverifikasi wajah.',
                        style: TextStyle(color: Colors.white.withValues(alpha: 0.85), fontSize: 16, height: 1.35),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            // Sign-in card ---------------------------------------------------------------------------------
            Transform.translate(
              offset: const Offset(0, -44),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 20),
                child: AppCard(
                  padding: const EdgeInsets.fromLTRB(20, 24, 20, 20),
                  child: AutofillGroup(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text('Masuk', style: theme.textTheme.headlineSmall),
                        const SizedBox(height: 4),
                        Text('Gunakan ID atau email dari administrator.',
                            style: theme.textTheme.bodyMedium?.copyWith(color: AppColors.muted)),
                        const SizedBox(height: 20),
                        if (_error != null) ...[MessageBox(text: _error!), const SizedBox(height: 14)],
                        TextField(
                          controller: _login,
                          autocorrect: false,
                          enableSuggestions: false,
                          autofillHints: const [AutofillHints.username],
                          textInputAction: TextInputAction.next,
                          decoration: const InputDecoration(
                            hintText: 'ID atau email',
                            prefixIcon: Icon(Icons.badge_outlined),
                          ),
                        ),
                        const SizedBox(height: 12),
                        TextField(
                          controller: _password,
                          obscureText: _obscure,
                          autofillHints: const [AutofillHints.password],
                          onSubmitted: (_) => _submit(),
                          decoration: InputDecoration(
                            hintText: 'Password',
                            prefixIcon: const Icon(Icons.lock_outline_rounded),
                            suffixIcon: IconButton(
                              tooltip: _obscure ? 'Tampilkan password' : 'Sembunyikan password',
                              icon: Icon(_obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                              onPressed: () => setState(() => _obscure = !_obscure),
                            ),
                          ),
                        ),
                        const SizedBox(height: 22),
                        GradientButton(label: 'Masuk', icon: Icons.login_rounded, loading: _busy, onPressed: _submit),
                      ],
                    ),
                  ),
                ),
              ),
            ),
            ListenableBuilder(
              listenable: widget.state,
              builder: (context, _) => Padding(
                padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(Icons.dns_outlined, size: 16, color: AppColors.muted),
                    const SizedBox(width: 6),
                    Flexible(
                      child: Text(
                        widget.state.serverUrl,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.bodySmall?.copyWith(color: AppColors.muted),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            Text('Lupa password? Hubungi administrator.', style: theme.textTheme.bodySmall?.copyWith(color: AppColors.muted)),
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }
}

class _Glow extends StatelessWidget {
  const _Glow({required this.size});

  final double size;

  @override
  Widget build(BuildContext context) => IgnorePointer(
        child: Container(
          width: size,
          height: size,
          decoration: BoxDecoration(shape: BoxShape.circle, color: Colors.white.withValues(alpha: 0.08)),
        ),
      );
}
