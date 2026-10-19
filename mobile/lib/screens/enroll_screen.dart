import 'package:flutter/material.dart';

import '../api.dart';
import '../app_state.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/ui.dart';
import 'challenge_screen.dart';

class EnrollScreen extends StatefulWidget {
  const EnrollScreen({super.key, required this.state});

  final AppState state;

  @override
  State<EnrollScreen> createState() => _EnrollScreenState();
}

class _EnrollScreenState extends State<EnrollScreen> {
  Map<String, dynamic>? _bio;
  Map<String, dynamic>? _consent;
  String? _error;
  bool _busy = false;
  bool _agreed = false;

  Api get _api => widget.state.api;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final bio = await _api.get('/biometrics/me') as Map<String, dynamic>;
      Map<String, dynamic>? consent;
      if (bio['consent_current'] != true) consent = await _api.get('/biometrics/consent-text') as Map<String, dynamic>;
      if (mounted) {
        setState(() {
          _bio = bio;
          _consent = consent;
          _error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _accept() async {
    setState(() => _busy = true);
    try {
      await _api.post('/biometrics/consent', json: {'consent_version': _consent!['version'], 'accepted': true});
      await _load();
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _enroll() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => ChallengeScreen(
          state: widget.state,
          title: 'Daftar wajah',
          startLabel: 'Mulai pendaftaran',
          start: () async {
            final res = await _api.post('/biometrics/enrollments') as Map<String, dynamic>;
            final id = res['enrollment_id'];
            return ChallengeSession(
              res['challenge'] as Map<String, dynamic>,
              (frames) async => await _api.postFrames('/biometrics/enrollments/$id/submit', frames)
                  as Map<String, dynamic>,
            );
          },
          onAccepted: (_) async => 'Wajah Anda berhasil didaftarkan.\nSekarang Anda bisa check-in dan check-out.',
        ),
      ),
    );
    if (mounted) _load();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final bio = _bio;
    final status = bio?['status'] as String?;
    final active = status == 'active';
    return Scaffold(
      appBar: AppBar(title: const Text('Verifikasi wajah')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
          children: [
            if (_error != null) ...[MessageBox(text: _error!), const SizedBox(height: 12)],
            if (bio == null && _error == null)
              const Padding(padding: EdgeInsets.all(40), child: Center(child: CircularProgressIndicator())),
            if (bio != null) ...[
              _Hero(active: active, status: status ?? ''),
              const SizedBox(height: 16),
              if (bio['verification_available'] != true) ...[
                const MessageBox(
                  tone: Tone.warning,
                  text: 'Verifikasi wajah sedang tidak tersedia di server. Coba lagi nanti.',
                ),
                const SizedBox(height: 16),
              ],
              if (_consent != null)
                _consentCard(theme)
              else if (bio['can_start_enrollment'] == true) ...[
                const SectionHeader(title: 'Sebelum mulai'),
                const AppCard(
                  child: Column(
                    children: [
                      _Tip(icon: Icons.light_mode_rounded, title: 'Cahaya cukup', text: 'Hadapkan wajah ke sumber cahaya. Layar akan menyala terang untuk membantu.'),
                      _Tip(icon: Icons.masks_rounded, title: 'Wajah terlihat jelas', text: 'Lepas masker, topi, atau kacamata hitam.'),
                      _Tip(icon: Icons.stay_current_portrait_rounded, title: 'HP sejajar mata', text: 'Pegang HP dengan stabil, lalu tatap kamera beberapa detik.', last: true),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
                Text(
                  'Sisa percobaan hari ini: ${bio['attempts_remaining_today']}',
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodySmall?.copyWith(color: AppColors.muted),
                ),
                const SizedBox(height: 14),
                GradientButton(
                  label: active ? 'Daftar ulang wajah' : 'Daftarkan wajah',
                  icon: Icons.face_rounded,
                  onPressed: bio['verification_available'] == true ? _enroll : null,
                ),
              ] else
                MessageBox(
                  tone: active ? Tone.success : Tone.warning,
                  icon: active ? Icons.verified_user_rounded : Icons.lock_outline_rounded,
                  text: active
                      ? 'Wajah Anda sudah terdaftar dan aktif.'
                      : 'Pendaftaran tidak dapat dimulai saat ini. Hubungi administrator.',
                ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _consentCard(ThemeData theme) => AppCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Row(
              children: [
                IconBadge(icon: Icons.privacy_tip_rounded, color: AppColors.brand),
                SizedBox(width: 12),
                Expanded(child: Text('Persetujuan data biometrik', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800))),
              ],
            ),
            const SizedBox(height: 14),
            Container(
              constraints: const BoxConstraints(maxHeight: 220),
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: theme.brightness == Brightness.dark ? const Color(0xFF18233A) : const Color(0xFFF6F8FC),
                borderRadius: BorderRadius.circular(14),
              ),
              child: SingleChildScrollView(
                child: Text(_consent!['text'] as String? ?? '', style: theme.textTheme.bodyMedium?.copyWith(height: 1.45)),
              ),
            ),
            const SizedBox(height: 8),
            CheckboxListTile(
              value: _agreed,
              onChanged: (v) => setState(() => _agreed = v ?? false),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              title: const Text('Saya telah membaca dan menyetujui penggunaan data wajah saya untuk absensi.'),
            ),
            const SizedBox(height: 8),
            GradientButton(label: 'Setuju & lanjutkan', loading: _busy, onPressed: _agreed ? _accept : null),
          ],
        ),
      );
}

class _Hero extends StatelessWidget {
  const _Hero({required this.active, required this.status});

  final bool active;
  final String status;

  @override
  Widget build(BuildContext context) => AppCard(
        padding: const EdgeInsets.symmetric(vertical: 24, horizontal: 18),
        child: Column(
          children: [
            Container(
              width: 96,
              height: 96,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: active ? AppColors.successGradient : AppColors.brandGradient,
                boxShadow: [
                  BoxShadow(
                    color: (active ? AppColors.success : AppColors.brand).withValues(alpha: 0.3),
                    blurRadius: 24,
                    offset: const Offset(0, 10),
                  ),
                ],
              ),
              child: Icon(active ? Icons.verified_user_rounded : Icons.face_retouching_natural_rounded, color: Colors.white, size: 48),
            ),
            const SizedBox(height: 16),
            Text(
              active ? 'Wajah Anda sudah terdaftar' : 'Daftarkan wajah Anda',
              style: Theme.of(context).textTheme.titleLarge,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 6),
            Text(
              active
                  ? 'Anda bisa check-in dan check-out dengan verifikasi wajah.'
                  : 'Cukup sekali. Setelah itu, absen hanya perlu menatap kamera beberapa detik.',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: AppColors.muted),
            ),
            const SizedBox(height: 12),
            StatusPill(label: biometricText[status] ?? status, tone: active ? Tone.success : Tone.warning),
          ],
        ),
      );
}

class _Tip extends StatelessWidget {
  const _Tip({required this.icon, required this.title, required this.text, this.last = false});

  final IconData icon;
  final String title;
  final String text;
  final bool last;

  @override
  Widget build(BuildContext context) => Padding(
        padding: EdgeInsets.only(bottom: last ? 0 : 16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            IconBadge(icon: icon, color: AppColors.brand, size: 40),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
                  const SizedBox(height: 2),
                  Text(text, style: const TextStyle(color: AppColors.muted, height: 1.35)),
                ],
              ),
            ),
          ],
        ),
      );
}
