from flask import Blueprint, jsonify, request

bp = Blueprint('rockstar', __name__, url_prefix='/api/rockstar',
               template_folder='templates')


@bp.route('/auth-url')
def auth_url():
    return jsonify({'url': 'https://www.rockstargames.com/'})


@bp.route('/connect', methods=['POST'])
def connect():
    from .rockstar import connect as _connect
    data  = request.get_json(silent=True) or {}
    token = (data.get('code') or data.get('token') or '').strip()
    if not token:
        return jsonify({'error': 'No token provided'}), 400
    ok, msg = _connect(token)
    if not ok:
        return jsonify({'error': msg}), 401
    return jsonify({'status': 'connected', 'username': msg})


@bp.route('/disconnect', methods=['POST'])
def disconnect():
    from .rockstar import disconnect as _disconnect
    _disconnect()
    return jsonify({'status': 'disconnected'})


@bp.route('/status')
def status():
    from .rockstar import is_connected, get_username
    connected = is_connected()
    return jsonify({
        'connected': connected,
        'username':  get_username() if connected else None,
    })


@bp.route('/start-launcher', methods=['POST'])
def start_launcher():
    from plugins.rockstar import plugin
    return jsonify(plugin.start_launcher())


@bp.route('/open-folder', methods=['POST'])
def open_folder_route():
    import os
    from . import _find_wine_launcher_config
    from runners.installdir import open_folder
    prefix, _wine_bin, _exe = _find_wine_launcher_config()
    if not prefix:
        return jsonify({'status': 'error', 'message': 'No Wine prefix configured'}), 400
    open_folder(os.path.join(prefix, 'drive_c', 'Program Files', 'Rockstar Games'))
    return jsonify({'status': 'ok'})


@bp.route('/sync', methods=['POST'])
def sync():
    from .rockstar import start_library_sync, is_connected
    if not is_connected():
        return jsonify({'error': 'Not connected'}), 401
    result = start_library_sync()
    if result.get('status') == 'already_running':
        return jsonify({'status': 'already_running'})
    return jsonify({'status': 'started'})


@bp.route('/sync-local', methods=['POST'])
def sync_local():
    from .rockstar import start_local_library_sync
    return jsonify(start_local_library_sync())


@bp.route('/sync-status')
def sync_status():
    from .rockstar import get_sync_state
    return jsonify(get_sync_state())


@bp.route('/uninstall/<int:appid>', methods=['POST'])
def uninstall(appid):
    from plugins.rockstar import plugin
    return jsonify(plugin.uninstall_game(appid))


@bp.route('/rescrape/<int:appid>', methods=['POST'])
def rescrape(appid):
    from plugins.rockstar import plugin
    from database import update_game_data
    meta = plugin.rescrape(appid)
    if meta is None:
        return jsonify({'status': 'error', 'message': 'Game not found'}), 404
    update_game_data(appid, **meta)
    return jsonify({'status': 'success', 'data': meta})
