// Shared building blocks so every screen speaks the same visual language.

import 'package:flutter/material.dart';

import '../theme.dart';

/// White rounded card with a soft shadow.
class AppCard extends StatelessWidget {
  const AppCard({super.key, required this.child, this.padding = const EdgeInsets.all(18), this.onTap});

  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(22),
        boxShadow: dark
            ? null
            : const [BoxShadow(color: Color(0x0D0F172A), blurRadius: 18, offset: Offset(0, 3))],
      ),
      child: Material(
        color: Theme.of(context).cardTheme.color,
        borderRadius: BorderRadius.circular(22),
        clipBehavior: Clip.antiAlias,
        child: InkWell(onTap: onTap, child: Padding(padding: padding, child: child)),
      ),
    );
  }
}

/// Icon on a tinted rounded square.
class IconBadge extends StatelessWidget {
  const IconBadge({super.key, required this.icon, required this.color, this.size = 44, this.background});

  final IconData icon;
  final Color color;
  final double size;
  final Color? background;

  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: background ?? color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(size * 0.32),
        ),
        child: Icon(icon, color: color, size: size * 0.52),
      );
}

enum Tone { brand, success, warning, danger, neutral }

({Color fg, Color bg}) toneColors(Tone tone, {bool dark = false}) => switch (tone) {
      Tone.brand => (fg: dark ? const Color(0xFF9DB8FF) : AppColors.brand, bg: dark ? const Color(0xFF1B2A4C) : AppColors.brandSoft),
      Tone.success => (fg: dark ? const Color(0xFF6EE7A0) : AppColors.success, bg: dark ? const Color(0xFF123323) : AppColors.successSoft),
      Tone.warning => (fg: dark ? const Color(0xFFFBBF6A) : AppColors.warning, bg: dark ? const Color(0xFF3A2A12) : AppColors.warningSoft),
      Tone.danger => (fg: dark ? const Color(0xFFFF8A8A) : AppColors.danger, bg: dark ? const Color(0xFF3B1717) : AppColors.dangerSoft),
      Tone.neutral => (fg: dark ? const Color(0xFFB4C0D6) : AppColors.muted, bg: dark ? const Color(0xFF1C2638) : const Color(0xFFEFF2F7)),
    };

/// Small rounded status label, e.g. "Tepat waktu".
class StatusPill extends StatelessWidget {
  const StatusPill({super.key, required this.label, this.tone = Tone.neutral, this.icon});

  final String label;
  final Tone tone;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final c = toneColors(tone, dark: Theme.of(context).brightness == Brightness.dark);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(color: c.bg, borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[Icon(icon, size: 14, color: c.fg), const SizedBox(width: 4)],
          Text(label, style: TextStyle(color: c.fg, fontSize: 12, fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }
}

/// Inline message box (errors, information).
class MessageBox extends StatelessWidget {
  const MessageBox({super.key, required this.text, this.tone = Tone.danger, this.icon});

  final String text;
  final Tone tone;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final c = toneColors(tone, dark: Theme.of(context).brightness == Brightness.dark);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(color: c.bg, borderRadius: BorderRadius.circular(16)),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon ?? (tone == Tone.danger ? Icons.error_outline_rounded : Icons.info_outline_rounded), color: c.fg, size: 22),
          const SizedBox(width: 10),
          Expanded(child: Text(text, style: TextStyle(color: c.fg, fontWeight: FontWeight.w600, height: 1.35))),
        ],
      ),
    );
  }
}

/// Full-width primary action with the brand gradient.
class GradientButton extends StatelessWidget {
  const GradientButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
    this.loading = false,
    this.gradient = AppColors.brandGradient,
    this.height = 56,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final bool loading;
  final Gradient gradient;
  final double height;

  @override
  Widget build(BuildContext context) {
    final enabled = onPressed != null && !loading;
    return AnimatedOpacity(
      duration: const Duration(milliseconds: 200),
      opacity: enabled || loading ? 1 : 0.45,
      child: Container(
        height: height,
        decoration: BoxDecoration(
          gradient: gradient,
          borderRadius: BorderRadius.circular(18),
          boxShadow: enabled
              ? [BoxShadow(color: gradient.colors.first.withValues(alpha: 0.28), blurRadius: 16, offset: const Offset(0, 4))]
              : null,
        ),
        child: Material(
          color: Colors.transparent,
          borderRadius: BorderRadius.circular(18),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: enabled ? onPressed : null,
            child: Center(
              child: loading
                  ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2.6, color: Colors.white))
                  : Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (icon != null) ...[Icon(icon, color: Colors.white), const SizedBox(width: 10)],
                        Text(label, style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w800)),
                      ],
                    ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Section title with an optional trailing action.
class SectionHeader extends StatelessWidget {
  const SectionHeader({super.key, required this.title, this.action, this.onAction});

  final String title;
  final String? action;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 8, 0, 8),
        child: Row(
          children: [
            Expanded(child: Text(title, style: Theme.of(context).textTheme.titleMedium)),
            if (action != null) TextButton(onPressed: onAction, child: Text(action!)),
          ],
        ),
      );
}

/// Friendly placeholder for empty lists.
class EmptyState extends StatelessWidget {
  const EmptyState({super.key, required this.icon, required this.title, this.subtitle});

  final IconData icon;
  final String title;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 16),
      child: Column(
        children: [
          IconBadge(icon: icon, color: theme.colorScheme.primary, size: 60),
          const SizedBox(height: 12),
          Text(title, style: theme.textTheme.titleMedium, textAlign: TextAlign.center),
          if (subtitle != null) ...[
            const SizedBox(height: 4),
            Text(subtitle!, style: theme.textTheme.bodyMedium?.copyWith(color: AppColors.muted), textAlign: TextAlign.center),
          ],
        ],
      ),
    );
  }
}

/// Initials in a circle, used as a lightweight avatar.
class InitialsAvatar extends StatelessWidget {
  const InitialsAvatar({super.key, required this.name, this.size = 48});

  final String name;
  final double size;

  @override
  Widget build(BuildContext context) {
    final parts = name.trim().split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
    final initials = parts.isEmpty ? '?' : (parts.first[0] + (parts.length > 1 ? parts.last[0] : '')).toUpperCase();
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: Colors.white.withValues(alpha: 0.18),
        border: Border.all(color: Colors.white.withValues(alpha: 0.5), width: 1.5),
      ),
      alignment: Alignment.center,
      child: Text(initials, style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: size * 0.36)),
    );
  }
}

/// The Absen mark (face-scan corners + smile), matching the launcher icon.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 64, this.inverted = false});

  /// Edge length of the rounded square.
  final double size;

  /// false: white tile with a blue glyph (on gradients); true: gradient tile with a white glyph.
  final bool inverted;

  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: inverted ? null : Colors.white,
          gradient: inverted ? AppColors.brandGradient : null,
          borderRadius: BorderRadius.circular(size * 0.3),
          boxShadow: const [BoxShadow(color: Color(0x26000000), blurRadius: 16, offset: Offset(0, 4))],
        ),
        child: CustomPaint(painter: _BrandGlyph(inverted ? Colors.white : AppColors.brand)),
      );
}

class _BrandGlyph extends CustomPainter {
  const _BrandGlyph(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final n = size.shortestSide, c = Offset(size.width / 2, size.height / 2), g = n * 0.56;
    final stroke = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = g * 0.075
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final half = g / 2, arm = g * 0.26;
    for (final sx in const [-1.0, 1.0]) {
      for (final sy in const [-1.0, 1.0]) {
        final corner = c + Offset(sx * half, sy * half);
        canvas.drawPath(
          Path()
            ..moveTo(corner.dx - sx * arm, corner.dy)
            ..lineTo(corner.dx, corner.dy)
            ..lineTo(corner.dx, corner.dy - sy * arm),
          stroke,
        );
      }
    }
    final fill = Paint()..color = color;
    canvas.drawCircle(c + Offset(-g * 0.15, -g * 0.07), g * 0.055, fill);
    canvas.drawCircle(c + Offset(g * 0.15, -g * 0.07), g * 0.055, fill);
    canvas.drawArc(
      Rect.fromCenter(center: c + Offset(0, g * 0.02), width: g * 0.4, height: g * 0.32),
      0.44,
      2.27,
      false,
      stroke,
    );
  }

  @override
  bool shouldRepaint(covariant _BrandGlyph old) => old.color != color;
}
