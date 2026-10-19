import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../app_state.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/ui.dart';
import 'challenge_screen.dart';
import 'enroll_screen.dart';
import 'history_screen.dart';
import 'settings_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.state});

  final AppState state;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  Map<String, dynamic>? _status;
  List<dynamic> _recent = [];
  String? _error;
  bool _loading = true;
  DateTime _now = DateTime.now();
  Timer? _clock;

  Api get _api => widget.state.api;

  @override
  void initState() {
    super.initState();
    _clock = Timer.periodic(const Duration(seconds: 20), (_) => setState(() => _now = DateTime.now()));
    _load();
  }

  @override
  void dispose() {
    _clock?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final st = await _api.get('/attendance/status') as Map<String, dynamic>;
      final recent = await _api.get('/attendance/me', query: {'limit': '5'}) as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _status = st;
        _recent = recent['items'] as List? ?? [];
        _now = DateTime.now();
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _open(Widget screen) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => screen));
    if (mounted) _load();
  }

  Future<void> _confirmSignOut() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Keluar dari akun?'),
        content: const Text('Anda perlu masuk lagi untuk absen.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Batal')),
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Keluar')),
        ],
      ),
    );
    if (ok == true) await widget.state.signOut();
  }

  void _attendance(String purpose) {
    final isOut = purpose == 'check_out';
    _open(
      ChallengeScreen(
        state: widget.state,
        title: isOut ? 'Check-out' : 'Check-in',
        startLabel: isOut ? 'Mulai check-out' : 'Mulai check-in',
        start: () async {
          final ch = await _api.post('/attendance/challenges', json: {'purpose': purpose}) as Map<String, dynamic>;
          return ChallengeSession(
            ch,
            (frames) async => await _api.postFrames('/attendance/challenges/${ch['id']}/submit', frames)
                as Map<String, dynamic>,
          );
        },
        onAccepted: (result) async {
          // The idempotency key makes a retried request return the same record instead of a duplicate.
          final rec = await _api.post(
            isOut ? '/attendance/check-out' : '/attendance/check-in',
            json: {'challenge_id': result['challenge_id']},
            headers: {'Idempotency-Key': newIdempotencyKey()},
          ) as Map<String, dynamic>;
          if (isOut) {
            return 'Check-out tercatat pukul ${formatTime(rec['check_out_at'])}.\n'
                'Durasi kerja: ${formatMinutes(rec['worked_minutes'])}';
          }
          final late = rec['late_minutes'] as int? ?? 0;
          return 'Check-in tercatat pukul ${formatTime(rec['check_in_at'])}.\n'
              '${late > 0 ? 'Terlambat ${formatMinutes(late)}' : 'Tepat waktu'}';
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final me = widget.state.me ?? {};
    final name = (me['full_name'] as String?) ?? '';
    final top = MediaQuery.paddingOf(context).top;
    return Scaffold(
      body: Stack(
        children: [
          Container(
            height: 300 + top,
            decoration: const BoxDecoration(
              gradient: AppColors.brandGradient,
              borderRadius: BorderRadius.vertical(bottom: Radius.circular(36)),
            ),
          ),
          Positioned(
            right: -60,
            top: top - 20,
            child: Container(
              width: 220,
              height: 220,
              decoration: BoxDecoration(shape: BoxShape.circle, color: Colors.white.withValues(alpha: 0.07)),
            ),
          ),
          RefreshIndicator(
            onRefresh: _load,
            edgeOffset: top,
            child: ListView(
              padding: EdgeInsets.fromLTRB(20, top + 12, 20, 32),
              children: [
                _header(name),
                const SizedBox(height: 22),
                _clockRow(),
                const SizedBox(height: 20),
                if (_loading && _status == null)
                  const AppCard(child: SizedBox(height: 160, child: Center(child: CircularProgressIndicator())))
                else if (_error != null && _status == null)
                  AppCard(
                    child: Column(
                      children: [
                        MessageBox(text: _error!),
                        const SizedBox(height: 12),
                        OutlinedButton.icon(onPressed: _load, icon: const Icon(Icons.refresh), label: const Text('Muat ulang')),
                      ],
                    ),
                  )
                else if (_status != null) ...[
                  _AttendanceCard(
                    status: _status!,
                    recent: _recent,
                    now: _now,
                    onAction: _attendance,
                  ),
                  const SizedBox(height: 14),
                  _faceCard(_status!['biometric_status'] as String? ?? 'not_enrolled'),
                  const SizedBox(height: 10),
                  SectionHeader(
                    title: 'Riwayat terbaru',
                    action: 'Lihat semua',
                    onAction: () => _open(HistoryScreen(state: widget.state)),
                  ),
                  if (_recent.isEmpty)
                    const AppCard(
                      child: EmptyState(
                        icon: Icons.event_note_rounded,
                        title: 'Belum ada absensi',
                        subtitle: 'Catatan check-in dan check-out Anda akan muncul di sini.',
                      ),
                    )
                  else
                    for (final r in _recent.cast<Map<String, dynamic>>())
                      Padding(padding: const EdgeInsets.only(bottom: 10), child: RecordTile(record: r)),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _header(String name) => Row(
        children: [
          InitialsAvatar(name: name),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(greeting(_now), style: TextStyle(color: Colors.white.withValues(alpha: 0.8), fontSize: 14)),
                const SizedBox(height: 2),
                Text(
                  name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w800),
                ),
              ],
            ),
          ),
          PopupMenuButton<String>(
            icon: const Icon(Icons.more_vert_rounded, color: Colors.white),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
            onSelected: (v) {
              if (v == 'history') _open(HistoryScreen(state: widget.state));
              if (v == 'settings') _open(SettingsScreen(state: widget.state));
              if (v == 'logout') _confirmSignOut();
            },
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'history', child: ListTile(leading: Icon(Icons.history_rounded), title: Text('Riwayat'))),
              PopupMenuItem(value: 'settings', child: ListTile(leading: Icon(Icons.tune_rounded), title: Text('Pengaturan'))),
              PopupMenuItem(value: 'logout', child: ListTile(leading: Icon(Icons.logout_rounded), title: Text('Keluar'))),
            ],
          ),
        ],
      );

  Widget _clockRow() {
    final online = _error == null;
    return Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(formatLongDate(_now), style: TextStyle(color: Colors.white.withValues(alpha: 0.85), fontSize: 14)),
                const SizedBox(height: 2),
                Text(
                  formatTime(_now.toIso8601String()),
                  style: const TextStyle(color: Colors.white, fontSize: 44, fontWeight: FontWeight.w900, letterSpacing: -1.5, height: 1.05),
                ),
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.16), borderRadius: BorderRadius.circular(999)),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(online ? Icons.wifi_tethering_rounded : Icons.wifi_off_rounded, color: Colors.white, size: 16),
                const SizedBox(width: 6),
                Text(
                  online ? 'Terhubung' : 'Offline',
                  style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: 12),
                ),
              ],
            ),
          ),
        ],
      );
  }

  Widget _faceCard(String bio) {
    final active = bio == 'active';
    return AppCard(
      onTap: () => _open(EnrollScreen(state: widget.state)),
      child: Row(
        children: [
          IconBadge(
            icon: active ? Icons.verified_user_rounded : Icons.face_retouching_natural_rounded,
            color: active ? AppColors.success : AppColors.warning,
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Verifikasi wajah', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 2),
                Text(
                  active ? 'Siap untuk absen' : 'Ketuk untuk mendaftarkan wajah',
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(color: AppColors.muted),
                ),
              ],
            ),
          ),
          StatusPill(label: biometricText[bio] ?? bio, tone: active ? Tone.success : Tone.warning),
          const SizedBox(width: 4),
          const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
        ],
      ),
    );
  }
}

/// Today's shift, its state and the main check-in / check-out action.
class _AttendanceCard extends StatelessWidget {
  const _AttendanceCard({required this.status, required this.recent, required this.now, required this.onAction});

  final Map<String, dynamic> status;
  final List<dynamic> recent;
  final DateTime now;
  final void Function(String purpose) onAction;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final today = (status['today'] as List? ?? const []).cast<Map<String, dynamic>>();
    final open = status['open_record'] as Map<String, dynamic>?;
    final canIn = status['can_check_in'] == true, canOut = status['can_check_out'] == true;
    final geofence = status['geofence_required'] == true;
    final occ = open != null
        ? today.firstWhere((o) => o['record_id'] == open['id'], orElse: () => today.isEmpty ? <String, dynamic>{} : today.first)
        : (today.isEmpty ? null : today.first);
    final recordId = occ?['record_id'];
    final record = open ??
        recent.cast<Map<String, dynamic>>().where((r) => recordId != null && r['id'] == recordId).firstOrNull;
    final done = record != null && record['check_out_at'] != null;

    final (String label, Tone tone, IconData icon) pill = open != null
        ? ('Sudah check-in', Tone.success, Icons.login_rounded)
        : done
            ? ('Selesai', Tone.brand, Icons.task_alt_rounded)
            : canIn
                ? ('Check-in dibuka', Tone.warning, Icons.lock_open_rounded)
                : ('Belum dibuka', Tone.neutral, Icons.lock_clock_rounded);
    final purpose = canOut ? 'check_out' : (canIn ? 'check_in' : null);
    final blockerCode = (canOut || canIn) ? null : (status['check_out_blocker'] ?? status['check_in_blocker']) as String?;
    final next = status['next_occurrence'] as Map<String, dynamic>?;

    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 18, 18, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const IconBadge(icon: Icons.schedule_rounded, color: AppColors.brand),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(occ == null ? 'Jadwal' : _dayLabel(occ['shift_date'] as String?, now),
                        style: theme.textTheme.bodySmall?.copyWith(color: AppColors.muted)),
                    Text(
                      occ == null || occ.isEmpty ? 'Tidak ada shift' : '${occ['shift_name']}',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleMedium,
                    ),
                    const SizedBox(height: 6),
                    StatusPill(label: pill.$1, tone: pill.$2, icon: pill.$3),
                  ],
                ),
              ),
            ],
          ),
          if (occ != null && occ.isNotEmpty) ...[
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: _TimeBox(
                    label: 'Masuk',
                    icon: Icons.login_rounded,
                    value: record != null ? formatTime(record['check_in_at']) : formatTime(occ['start_at']),
                    caption: record != null ? _lateCaption(record) : 'Jadwal',
                    tone: record != null ? ((record['late_minutes'] as int? ?? 0) > 0 ? Tone.warning : Tone.success) : Tone.neutral,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: _TimeBox(
                    label: 'Pulang',
                    icon: Icons.logout_rounded,
                    value: done ? formatTime(record['check_out_at']) : formatTime(occ['end_at']),
                    caption: done ? formatMinutes(record['worked_minutes']) : 'Jadwal',
                    tone: done ? Tone.brand : Tone.neutral,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                const Icon(Icons.info_outline_rounded, size: 16, color: AppColors.muted),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    open != null || done
                        ? 'Check-out ${formatTime(occ['check_out_opens_at'])}–${formatTime(occ['check_out_closes_at'])}'
                        : 'Check-in ${formatTime(occ['check_in_opens_at'])}–${formatTime(occ['check_in_closes_at'])} · '
                            'check-out ${formatTime(occ['check_out_opens_at'])}–${formatTime(occ['check_out_closes_at'])}',
                    style: theme.textTheme.bodySmall?.copyWith(color: AppColors.muted),
                  ),
                ),
              ],
            ),
          ],
          const SizedBox(height: 16),
          if (geofence)
            const MessageBox(
              tone: Tone.warning,
              icon: Icons.location_off_rounded,
              text: 'Shift ini mewajibkan lokasi (geofence). Untuk sementara gunakan versi web.',
            )
          else if (purpose != null)
            GradientButton(
              label: purpose == 'check_out' ? 'Check-out sekarang' : 'Check-in sekarang',
              icon: Icons.face_rounded,
              gradient: purpose == 'check_out' ? AppColors.warmGradient : AppColors.brandGradient,
              height: 60,
              onPressed: () => onAction(purpose),
            )
          else
            MessageBox(
              tone: done ? Tone.success : Tone.neutral,
              icon: done ? Icons.celebration_rounded : Icons.hourglass_empty_rounded,
              text: done
                  ? 'Absensi hari ini sudah lengkap. Terima kasih!'
                  : '${blocker(blockerCode)}${blockerCode == 'check_out_not_open_yet' && occ != null && occ.isNotEmpty ? ' Dibuka pukul ${formatTime(occ['check_out_opens_at'])}.' : ''}${next != null && blockerCode != 'already_checked_in' ? '\nBerikutnya: ${_dayLabel(next['shift_date'] as String?, now)}, '
                      'check-in ${formatTime(next['check_in_opens_at'])}–${formatTime(next['check_in_closes_at'])}' : ''}',
            ),
        ],
      ),
    );
  }

  static String _lateCaption(Map<String, dynamic> r) {
    final late = r['late_minutes'] as int? ?? 0;
    return late > 0 ? 'Terlambat ${formatMinutes(late)}' : 'Tepat waktu';
  }

  static String _dayLabel(String? shiftDate, DateTime now) {
    final d = shiftDate == null ? null : DateTime.tryParse(shiftDate);
    if (d == null) return 'Shift';
    final today = DateTime(now.year, now.month, now.day);
    final diff = DateTime(d.year, d.month, d.day).difference(today).inDays;
    if (diff == 0) return 'Shift hari ini';
    if (diff == 1) return 'Shift besok';
    return 'Shift ${formatDate(shiftDate)}';
  }
}

class _TimeBox extends StatelessWidget {
  const _TimeBox({required this.label, required this.icon, required this.value, required this.caption, required this.tone});

  final String label;
  final IconData icon;
  final String value;
  final String caption;
  final Tone tone;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final c = toneColors(tone, dark: dark);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: dark ? const Color(0xFF18233A) : const Color(0xFFF6F8FC),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 16, color: AppColors.muted),
              const SizedBox(width: 6),
              Text(label, style: const TextStyle(color: AppColors.muted, fontWeight: FontWeight.w600, fontSize: 13)),
            ],
          ),
          const SizedBox(height: 6),
          Text(value, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w900, letterSpacing: -0.5)),
          const SizedBox(height: 2),
          Text(caption, style: TextStyle(color: c.fg, fontWeight: FontWeight.w700, fontSize: 12)),
        ],
      ),
    );
  }
}
