// HTTP client for the Absen backend (native clients use a bearer session token, never cookies).

import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';

class ApiException implements Exception {
  ApiException(this.status, this.code, this.message);

  /// HTTP status; 0 means the server could not be reached.
  final int status;
  final String code;
  final String message;

  @override
  String toString() => message;
}

String newIdempotencyKey() {
  final r = Random.secure();
  return List.generate(16, (_) => r.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
}

class Api {
  Api({required this.baseUrl, this.token, http.Client? client}) : _client = client ?? http.Client();

  /// e.g. http://192.168.100.11:8000 (the /api/v1 prefix is added here).
  String baseUrl;
  String? token;
  final http.Client _client;

  /// Called when the server rejects the session (expired, revoked, signed out elsewhere).
  void Function()? onUnauthorized;

  Uri _uri(String path, [Map<String, String>? query]) {
    final base = baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl;
    return Uri.parse('$base/api/v1$path').replace(queryParameters: query);
  }

  Map<String, String> _headers([Map<String, String>? extra]) => {
        'Accept': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
        ...?extra,
      };

  Future<dynamic> _send(Future<http.Response> Function() request, {Duration timeout = const Duration(seconds: 20)}) async {
    final http.Response res;
    try {
      res = await request().timeout(timeout);
    } on TimeoutException {
      throw ApiException(0, 'timeout', 'Server tidak merespons. Periksa koneksi Wi-Fi dan alamat server.');
    } on Exception {
      throw ApiException(0, 'network', 'Tidak dapat terhubung ke server. Pastikan HP dan laptop di Wi-Fi yang sama.');
    }
    final body = res.body.isEmpty ? null : _tryJson(res.body);
    if (res.statusCode >= 200 && res.statusCode < 300) return body;
    final err = body is Map<String, dynamic> ? body['error'] : null;
    final code = err is Map ? (err['code'] as String? ?? 'error') : 'error';
    final message = err is Map ? (err['message'] as String? ?? 'Permintaan gagal.') : 'Permintaan gagal (${res.statusCode}).';
    if (res.statusCode == 401 && token != null) onUnauthorized?.call();
    throw ApiException(res.statusCode, code, message);
  }

  static dynamic _tryJson(String s) {
    try {
      return jsonDecode(s);
    } on FormatException {
      return null;
    }
  }

  Future<dynamic> get(String path, {Map<String, String>? query}) =>
      _send(() => _client.get(_uri(path, query), headers: _headers()));

  Future<dynamic> post(String path, {Object? json, Map<String, String>? headers}) => _send(
        () => _client.post(
          _uri(path),
          headers: _headers({'Content-Type': 'application/json', ...?headers}),
          body: jsonEncode(json ?? {}),
        ),
      );

  /// Multipart upload of JPEG frames (field [field], ordered file names, like the web client).
  Future<dynamic> postFrames(String path, List<Uint8List> frames, {String field = 'frames'}) => _send(
        () async {
          final req = http.MultipartRequest('POST', _uri(path))..headers.addAll(_headers());
          for (var i = 0; i < frames.length; i++) {
            req.files.add(
              // The server only accepts parts declared as image/jpeg or image/png (and checks the bytes too).
              http.MultipartFile.fromBytes(
                field,
                frames[i],
                filename: 'frame-${i.toString().padLeft(3, '0')}.jpg',
                contentType: MediaType('image', 'jpeg'),
              ),
            );
          }
          return http.Response.fromStream(await _client.send(req));
        },
        timeout: const Duration(seconds: 90),
      );
}
