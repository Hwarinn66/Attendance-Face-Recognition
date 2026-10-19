import 'package:flutter/material.dart';

import '../api.dart';
import '../app_state.dart';
import '../theme.dart';
import '../widgets/ui.dart';

/// Shown when the administrator set a temporary password: it must be changed before anything else.
class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({super.key, required this.state});

  final AppState state;

  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  final _current = TextEditingController();
  final _next = TextEditingController();
  final _confirm = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    for (final c in [_current, _next, _confirm]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    if (_next.text != _confirm.text) {
      setState(() => _error = 'Konfirmasi password tidak sama.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.state.api.post(
        '/auth/change-password',
        json: {'current_password': _current.text, 'new_password': _next.text},
      );
      await widget.state.refreshMe();
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Ganti password'),
        actions: [TextButton(onPressed: widget.state.signOut, child: const Text('Keluar'))],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: [
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Row(
                  children: [
                    IconBadge(icon: Icons.key_rounded, color: AppColors.brand),
                    SizedBox(width: 12),
                    Expanded(child: Text('Buat password baru', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800))),
                  ],
                ),
                const SizedBox(height: 10),
                Text(
                  'Password Anda bersifat sementara. Gunakan minimal 12 karakter dengan campuran huruf besar, '
                  'huruf kecil, angka atau simbol.',
                  style: theme.textTheme.bodyMedium?.copyWith(color: AppColors.muted, height: 1.4),
                ),
                const SizedBox(height: 18),
                if (_error != null) ...[MessageBox(text: _error!), const SizedBox(height: 12)],
                TextField(
                  controller: _current,
                  obscureText: true,
                  decoration: const InputDecoration(hintText: 'Password saat ini', prefixIcon: Icon(Icons.lock_outline_rounded)),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _next,
                  obscureText: true,
                  decoration: const InputDecoration(hintText: 'Password baru', prefixIcon: Icon(Icons.lock_reset_rounded)),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _confirm,
                  obscureText: true,
                  onSubmitted: (_) => _submit(),
                  decoration: const InputDecoration(hintText: 'Ulangi password baru', prefixIcon: Icon(Icons.verified_outlined)),
                ),
                const SizedBox(height: 20),
                GradientButton(label: 'Simpan password', icon: Icons.save_rounded, loading: _busy, onPressed: _submit),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
