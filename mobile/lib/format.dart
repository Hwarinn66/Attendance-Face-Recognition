import 'package:intl/intl.dart';

const blockerText = {
  'no_schedule': 'Anda belum punya jadwal shift. Hubungi administrator.',
  'outside_check_in_window': 'Check-in belum/tidak dibuka saat ini.',
  'already_checked_in': 'Anda sudah check-in untuk shift ini.',
  'no_open_check_in': 'Tidak ada check-in yang perlu ditutup.',
  'check_out_not_open_yet': 'Check-out belum dibuka.',
  'biometric_not_enrolled': 'Daftarkan wajah Anda terlebih dahulu.',
  'biometric_pending_retry': 'Pendaftaran wajah terakhir gagal. Silakan daftar ulang.',
  'biometric_in_progress': 'Selesaikan pendaftaran wajah terlebih dahulu.',
  'biometric_verification_failed': 'Pendaftaran wajah terkunci. Hubungi administrator.',
  'biometric_suspended': 'Verifikasi wajah akun Anda ditangguhkan. Hubungi administrator.',
  'biometric_deleted': 'Data wajah Anda telah dihapus. Daftar ulang untuk absen.',
};

const biometricText = {
  'not_enrolled': 'Belum terdaftar',
  'in_progress': 'Sedang diproses',
  'verification_failed': 'Terkunci — hubungi administrator',
  'pending_retry': 'Perlu daftar ulang',
  'active': 'Aktif',
  'suspended': 'Ditangguhkan',
  'deleted': 'Dihapus',
};

String blocker(String? code) => code == null ? '' : (blockerText[code] ?? code);

DateTime? parseTime(Object? v) => v is String ? DateTime.tryParse(v)?.toLocal() : null;

String formatTime(Object? v) {
  final t = parseTime(v);
  return t == null ? '—' : DateFormat('HH:mm').format(t);
}

String formatDate(Object? v) {
  final t = v is String ? DateTime.tryParse(v) : null;
  return t == null ? '—' : DateFormat('EEE, d MMM yyyy').format(t);
}

String formatMinutes(Object? v) {
  if (v is! int) return '—';
  final h = v ~/ 60, m = v % 60;
  return h == 0 ? '$m mnt' : '$h j $m mnt';
}

String greeting(DateTime now) {
  final h = now.hour;
  if (h >= 4 && h < 11) return 'Selamat pagi';
  if (h >= 11 && h < 15) return 'Selamat siang';
  if (h >= 15 && h < 18) return 'Selamat sore';
  return 'Selamat malam';
}

String formatLongDate(DateTime t) => DateFormat('EEEE, d MMMM yyyy').format(t);

String formatDayNumber(Object? v) {
  final t = v is String ? DateTime.tryParse(v) : null;
  return t == null ? '—' : DateFormat('d').format(t);
}

String formatMonthShort(Object? v) {
  final t = v is String ? DateTime.tryParse(v) : null;
  return t == null ? '' : DateFormat('MMM').format(t).toUpperCase();
}

String formatWeekday(Object? v) {
  final t = v is String ? DateTime.tryParse(v) : null;
  return t == null ? '' : DateFormat('EEEE').format(t);
}
