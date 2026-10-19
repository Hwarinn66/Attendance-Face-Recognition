// Persisted app configuration and the signed-in session.

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';

const defaultServerUrl = 'http://192.168.100.11:8000';

class AppState extends ChangeNotifier {
  AppState._(this._prefs, this._secure, this.api);

  static const _kServer = 'server_url';
  static const _kMirror = 'mirror_frames';
  static const _kRotation = 'camera_rotation';
  static const _kToken = 'session_token';

  final SharedPreferences _prefs;
  final FlutterSecureStorage _secure;
  final Api api;
  Map<String, dynamic>? me;

  /// [client] is for tests (a mock HTTP client); the app uses the default client.
  static Future<AppState> load({http.Client? client}) async {
    final prefs = await SharedPreferences.getInstance().timeout(const Duration(seconds: 10));
    const secure = FlutterSecureStorage();
    String? token;
    try {
      token = await secure.read(key: _kToken).timeout(const Duration(seconds: 5));
    } on Object catch (e) {
      // A broken or slow keystore must not block start-up: the user simply signs in again.
      debugPrint('secure storage read failed: $e');
    }
    final api = Api(baseUrl: prefs.getString(_kServer) ?? defaultServerUrl, token: token, client: client);
    final state = AppState._(prefs, secure, api);
    api.onUnauthorized = state._dropSession;
    return state;
  }

  String get serverUrl => api.baseUrl;
  bool get signedIn => api.token != null && me != null;
  bool get mirrorFrames => _prefs.getBool(_kMirror) ?? false;

  /// Calibrated clockwise rotation for the front camera, or null if not calibrated yet.
  int? get cameraRotation => _prefs.getInt(_kRotation);

  Future<void> setServerUrl(String url) async {
    api.baseUrl = url.trim();
    await _prefs.setString(_kServer, api.baseUrl);
    notifyListeners();
  }

  Future<void> setMirrorFrames(bool v) async {
    await _prefs.setBool(_kMirror, v);
    notifyListeners();
  }

  Future<void> setCameraRotation(int? v) async {
    if (v == null) {
      await _prefs.remove(_kRotation);
    } else {
      await _prefs.setInt(_kRotation, v);
    }
  }

  /// Restores the session from secure storage; false if there is none or it expired.
  Future<bool> restore() async {
    if (api.token == null) return false;
    try {
      me = await api.get('/auth/me') as Map<String, dynamic>;
      notifyListeners();
      return true;
    } on ApiException catch (e) {
      if (e.status == 401) await _clearToken();
      return false;
    }
  }

  Future<void> signIn(String login, String password) async {
    final res = await api.post('/auth/token', json: {'login': login, 'password': password}) as Map<String, dynamic>;
    api.token = res['access_token'] as String;
    try {
      await _secure.write(key: _kToken, value: api.token).timeout(const Duration(seconds: 5));
    } on Object catch (e) {
      debugPrint('secure storage write failed (session kept in memory only): $e');
    }
    me = res['user'] as Map<String, dynamic>;
    notifyListeners();
  }

  Future<void> refreshMe() async {
    me = await api.get('/auth/me') as Map<String, dynamic>;
    notifyListeners();
  }

  Future<void> signOut() async {
    try {
      if (api.token != null) await api.post('/auth/logout');
    } on ApiException {
      // The local session is dropped regardless.
    }
    await _clearToken();
    me = null;
    notifyListeners();
  }

  void _dropSession() {
    me = null;
    _clearToken();
    notifyListeners();
  }

  Future<void> _clearToken() async {
    api.token = null;
    try {
      await _secure.delete(key: _kToken).timeout(const Duration(seconds: 5));
    } on Object catch (e) {
      debugPrint('secure storage delete failed: $e');
    }
  }
}
