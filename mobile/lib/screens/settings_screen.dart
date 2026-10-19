import 'package:flutter/material.dart';

import '../api.dart';
import '../app_state.dart';
import '../theme.dart';
import '../widgets/ui.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.state});

  final AppState state;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final _url = TextEditingController(text: widget.state.serverUrl);
  String? _status;
  bool? _ok;
  bool _testing = false;

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final url = _url.text.trim();
    final uri = Uri.tryParse(url);
    if (uri == null || !(uri.scheme == 'http' || uri.scheme == 'https') || uri.host.isEmpty) {
      setState(() {
        _ok = false;
        _status = 'Alamat tidak valid. Contoh: http://192.168.100.11:8000';
      });
      return;
    }
    await widget.state.setServerUrl(url);
    await _test();
  }

  Future<void> _test() async {
    setState(() {
      _testing = true;
      _status = null;
    });
    try {
      final res = await Api(baseUrl: widget.state.serverUrl).get('/health/ready') as Map<String, dynamic>;
      final checks = res['checks'] as Map<String, dynamic>? ?? {};
      setState(() {
        _ok = res['status'] == 'ok';
        _status = 'Terhubung · server ${res['status']} · verifikasi wajah ${checks['verification']}';
      });
    } on ApiException catch (e) {
      setState(() {
        _ok = false;
        _status = e.message;
      });
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Pengaturan')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: [
          const SectionHeader(title: 'Koneksi server'),
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextField(
                  controller: _url,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  decoration: const InputDecoration(hintText: 'http://192.168.100.11:8000', prefixIcon: Icon(Icons.dns_outlined)),
                ),
                const SizedBox(height: 8),
                Text('Alamat backend di laptop/server kantor.', style: theme.textTheme.bodySmall?.copyWith(color: AppColors.muted)),
                const SizedBox(height: 14),
                GradientButton(label: 'Simpan & uji koneksi', icon: Icons.wifi_find_rounded, loading: _testing, onPressed: _save),
                if (_status != null) ...[
                  const SizedBox(height: 12),
                  MessageBox(
                    text: _status!,
                    tone: _ok == true ? Tone.success : Tone.danger,
                    icon: _ok == true ? Icons.check_circle_outline_rounded : Icons.error_outline_rounded,
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 8),
          const SectionHeader(title: 'Kamera'),
          AppCard(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Column(
              children: [
                ListenableBuilder(
                  listenable: widget.state,
                  builder: (context, _) => SwitchListTile(
                    secondary: const IconBadge(icon: Icons.flip_rounded, color: AppColors.brand, size: 40),
                    title: const Text('Cerminkan gambar kamera', style: TextStyle(fontWeight: FontWeight.w700)),
                    subtitle: const Text('Aktifkan hanya bila arah kiri/kanan selalu terbaca terbalik.'),
                    value: widget.state.mirrorFrames,
                    onChanged: widget.state.setMirrorFrames,
                  ),
                ),
                const Divider(indent: 16, endIndent: 16),
                ListTile(
                  leading: const IconBadge(icon: Icons.screen_rotation_alt_rounded, color: AppColors.brand, size: 40),
                  title: const Text('Kalibrasi ulang orientasi', style: TextStyle(fontWeight: FontWeight.w700)),
                  subtitle: Text(
                    widget.state.cameraRotation == null
                        ? 'Otomatis saat kamera pertama dibuka.'
                        : 'Rotasi saat ini ${widget.state.cameraRotation}° · ketuk untuk mengulang',
                  ),
                  trailing: const Icon(Icons.restart_alt_rounded),
                  onTap: () async {
                    await widget.state.setCameraRotation(null);
                    if (!context.mounted) return;
                    setState(() {});
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Orientasi akan dikalibrasi ulang saat kamera dibuka.')),
                    );
                  },
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          const SectionHeader(title: 'Tentang'),
          const AppCard(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                IconBadge(icon: Icons.science_outlined, color: AppColors.warning, size: 40),
                SizedBox(width: 12),
                Expanded(
                  child: Text(
                    'Versi uji coba. Koneksi ke laptop memakai HTTP biasa di Wi-Fi lokal; '
                    'untuk penggunaan sungguhan server harus memakai HTTPS.',
                    style: TextStyle(height: 1.4),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
