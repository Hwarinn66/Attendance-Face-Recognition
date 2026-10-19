import 'package:flutter/material.dart';

import '../api.dart';
import '../app_state.dart';
import '../format.dart';
import '../theme.dart';
import '../widgets/ui.dart';

/// One attendance record: date badge, times and status labels.
class RecordTile extends StatelessWidget {
  const RecordTile({super.key, required this.record});

  final Map<String, dynamic> record;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    final late = record['late_minutes'] as int? ?? 0;
    final out = record['check_out_at'];
    final outStatus = record['check_out_status'] as String?;
    return AppCard(
      padding: const EdgeInsets.all(14),
      child: Row(
        children: [
          Container(
            width: 54,
            padding: const EdgeInsets.symmetric(vertical: 8),
            decoration: BoxDecoration(
              color: dark ? const Color(0xFF1B2A4C) : AppColors.brandSoft,
              borderRadius: BorderRadius.circular(14),
            ),
            child: Column(
              children: [
                Text(formatDayNumber(record['shift_date']),
                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w900, color: theme.colorScheme.primary, height: 1.1)),
                Text(formatMonthShort(record['shift_date']),
                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: theme.colorScheme.primary)),
              ],
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${formatWeekday(record['shift_date'])} · ${record['shift_name'] ?? ''}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800),
                ),
                const SizedBox(height: 4),
                Row(
                  children: [
                    const Icon(Icons.login_rounded, size: 14, color: AppColors.muted),
                    const SizedBox(width: 4),
                    Text(formatTime(record['check_in_at']), style: const TextStyle(fontWeight: FontWeight.w700)),
                    const SizedBox(width: 12),
                    const Icon(Icons.logout_rounded, size: 14, color: AppColors.muted),
                    const SizedBox(width: 4),
                    Text(out == null ? '—' : formatTime(out), style: const TextStyle(fontWeight: FontWeight.w700)),
                    if (record['worked_minutes'] != null) ...[
                      const SizedBox(width: 12),
                      const Icon(Icons.timelapse_rounded, size: 14, color: AppColors.muted),
                      const SizedBox(width: 4),
                      Flexible(
                        child: Text(formatMinutes(record['worked_minutes']),
                            overflow: TextOverflow.ellipsis, style: const TextStyle(color: AppColors.muted)),
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    late > 0
                        ? StatusPill(label: 'Terlambat ${formatMinutes(late)}', tone: Tone.warning)
                        : const StatusPill(label: 'Tepat waktu', tone: Tone.success),
                    if (out == null)
                      const StatusPill(label: 'Belum check-out', tone: Tone.neutral)
                    else if (outStatus == 'early')
                      const StatusPill(label: 'Pulang awal', tone: Tone.warning)
                    else if (outStatus == 'overtime')
                      const StatusPill(label: 'Lembur', tone: Tone.brand),
                    if (record['is_corrected'] == true) const StatusPill(label: 'Dikoreksi', tone: Tone.neutral),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key, required this.state});

  final AppState state;

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  List<Map<String, dynamic>>? _items;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final page = await widget.state.api.get('/attendance/me', query: {'limit': '50'}) as Map<String, dynamic>;
      if (mounted) {
        setState(() {
          _items = (page['items'] as List? ?? []).cast<Map<String, dynamic>>();
          _error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    final late = items?.where((r) => (r['late_minutes'] as int? ?? 0) > 0).length ?? 0;
    final worked = items?.map((r) => r['worked_minutes']).whereType<int>().toList() ?? const <int>[];
    return Scaffold(
      appBar: AppBar(title: const Text('Riwayat absensi')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
          children: [
            if (_error != null) ...[MessageBox(text: _error!), const SizedBox(height: 12)],
            if (items == null && _error == null)
              const Padding(padding: EdgeInsets.all(40), child: Center(child: CircularProgressIndicator())),
            if (items != null) ...[
              Row(
                children: [
                  Expanded(child: _Stat(label: 'Hadir', value: '${items.length}', icon: Icons.event_available_rounded, tone: Tone.brand)),
                  const SizedBox(width: 10),
                  Expanded(child: _Stat(label: 'Terlambat', value: '$late', icon: Icons.schedule_rounded, tone: Tone.warning)),
                  const SizedBox(width: 10),
                  Expanded(
                    child: _Stat(
                      label: 'Rata-rata',
                      value: worked.isEmpty ? '—' : formatMinutes(worked.reduce((a, b) => a + b) ~/ worked.length),
                      icon: Icons.timelapse_rounded,
                      tone: Tone.success,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              if (items.isEmpty)
                const AppCard(
                  child: EmptyState(
                    icon: Icons.event_note_rounded,
                    title: 'Belum ada absensi',
                    subtitle: 'Riwayat check-in dan check-out akan tampil di sini.',
                  ),
                ),
              for (final r in items) Padding(padding: const EdgeInsets.only(bottom: 10), child: RecordTile(record: r)),
            ],
          ],
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value, required this.icon, required this.tone});

  final String label;
  final String value;
  final IconData icon;
  final Tone tone;

  @override
  Widget build(BuildContext context) {
    final c = toneColors(tone, dark: Theme.of(context).brightness == Brightness.dark);
    return AppCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          IconBadge(icon: icon, color: c.fg, size: 34),
          const SizedBox(height: 10),
          FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.centerLeft,
            child: Text(value, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w900)),
          ),
          Text(label, style: const TextStyle(color: AppColors.muted, fontSize: 12, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}
