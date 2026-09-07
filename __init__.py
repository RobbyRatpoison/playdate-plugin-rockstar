import logging
import os
import sys

log = logging.getLogger(__name__)


def _find_native_launcher():
    """Return path to Launcher.exe on Windows, or None."""
    if sys.platform != 'win32':
        return None
    from runners.windows import find_installed_exe
    exe = find_installed_exe('Rockstar Games Launcher', 'Launcher.exe')
    if exe:
        return exe
    # Fallback: standard Program Files guess, in case the registry entry is
    # missing/named differently for some install method.
    for env in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432'):
        base = os.environ.get(env, '')
        if base:
            candidate = os.path.join(base, 'Rockstar Games', 'Launcher', 'Launcher.exe')
            if os.path.isfile(candidate):
                return candidate
    return None


def _find_wine_launcher_config():
    """Return (prefix, wine_bin, exe_path) for the Wine-installed Rockstar
    Games Launcher, or (None, None, None) if any piece is missing. Shared by
    launcher_status() and start_launcher()."""
    import json
    from config import CONFIG_PATH
    try:
        with open(CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    launcher_cfg = cfg.get('launchers', {}).get('rockstar', {})
    prefix   = launcher_cfg.get('prefix', '').strip()
    wine_bin = launcher_cfg.get('wine_bin', '').strip()

    from runners.wine import find_wine_binary
    if not wine_bin:
        wine_bin = find_wine_binary()
    if not wine_bin or not prefix or not os.path.isdir(prefix):
        return None, None, None

    for dirpath, _dirs, files in os.walk(prefix):
        if 'Launcher.exe' in files:
            return prefix, wine_bin, os.path.join(dirpath, 'Launcher.exe')
    return None, None, None


class RockstarPlugin:
    id       = 'rockstar'
    name     = 'Rockstar Games'
    platform = 'rockstar'
    label    = 'Rockstar Games'

    def register(self, app):
        from .routes import bp
        app.register_blueprint(bp)
        log.info('Rockstar Games plugin registered')

    def launcher_status(self):
        if sys.platform == 'win32':
            native = _find_native_launcher()
            if native:
                return {'available': True, 'detail': 'Launcher detected'}
            return {'available': False, 'detail': 'Rockstar Games Launcher not installed'}

        # Linux: require a Wine prefix with Launcher.exe
        import json
        from config import CONFIG_PATH
        try:
            with open(CONFIG_PATH, 'r') as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        launcher_cfg = cfg.get('launchers', {}).get('rockstar', {})
        prefix   = launcher_cfg.get('prefix', '').strip()
        wine_bin = launcher_cfg.get('wine_bin', '').strip()

        from runners.wine import find_wine_binary
        if not wine_bin:
            wine_bin = find_wine_binary()

        if not wine_bin:
            return {'available': False, 'detail': 'No Wine binary found'}
        if not prefix:
            return {'available': False, 'detail': 'Wine prefix not configured'}
        if not os.path.isdir(prefix):
            return {'available': False, 'detail': f'Prefix not found: {prefix}'}

        for _dirpath, _dirs, files in os.walk(prefix):
            if 'Launcher.exe' in files:
                return {'available': True, 'detail': 'Launcher ready'}

        return {
            'available': False,
            'detail': 'Launcher.exe not found in prefix — use Configure Launcher below',
        }

    def start_launcher(self):
        """Open the bare Rockstar Games Launcher (no specific game) so the
        user can sign in / verify ownership through the real app."""
        if sys.platform == 'win32':
            native = _find_native_launcher()
            if not native:
                return {'status': 'error', 'message': 'Rockstar Games Launcher is not installed'}
            try:
                os.startfile(native)
            except Exception as e:
                return {'status': 'error', 'message': f'Launch failed: {e}'}
            return {'status': 'success'}

        if sys.platform == 'darwin':
            return {'status': 'error', 'message': 'Not supported on macOS'}

        prefix, wine_bin, exe_path = _find_wine_launcher_config()
        if not exe_path:
            return {'status': 'error', 'message': 'Rockstar Games Launcher not found. Check Wine setup in Plugins settings.'}
        try:
            from runners.wine import run_in_prefix
            run_in_prefix(prefix, exe_path, wine_bin=wine_bin, env_extra={'WINEDEBUG': '-all'},
                          restart_session_if_running=True)
        except RuntimeError as e:
            return {'status': 'error', 'message': str(e)}
        except Exception as e:
            return {'status': 'error', 'message': f'Launch failed: {e}'}
        return {'status': 'success'}

    def on_startup(self):
        from .rockstar import resync_installed
        try:
            resync_installed()
        except Exception as e:
            log.warning(f'Rockstar resync_installed at startup failed: {e}')

    def on_shutdown(self):
        pass

    def on_uninstall(self):
        from .rockstar import disconnect
        disconnect()

    def launch_game(self, appid):
        from .rockstar import launch_game
        return launch_game(appid)

    def uninstall_game(self, appid):
        from .rockstar import uninstall_game
        return uninstall_game(appid)

    def resync_installed(self):
        from .rockstar import resync_installed
        resync_installed()

    def rescrape(self, appid):
        from .rockstar import scrape_single
        return scrape_single(appid) or None

    def fetch_description(self, appid, platform_id):
        return None

    def js_api(self):
        return {
            'uninstall_url':     '/api/rockstar/uninstall/{appid}',
            'uninstall_confirm': 'Uninstall this game?\n\n'
                                  'This tells the Rockstar Games Launcher to uninstall it -- the actual '
                                  'removal happens in the launcher, not immediately.',
            'scrape_url':     '/api/rockstar/rescrape/{appid}',
            'scrape_method':  'POST',
            'store_url':      'https://store.rockstargames.com/game/{slug}',
            'store_label':    'View on Rockstar Store ↗',
            'appid_label':    'Rockstar Title ID:',
            'sync_label':     'Sync Rockstar Library',
        }

    def manage_ui(self):
        native = _find_native_launcher()
        if native:
            launcher_section = {
                'title': 'Launcher',
                'items': [
                    {'type': 'text', 'content': 'Rockstar Games Launcher is installed — no additional setup needed.'},
                    {'type': 'button', 'label': 'Start Launcher', 'action': {
                        'type': 'call', 'fn': 'rockstarStartLauncher',
                    }},
                ],
            }
        elif sys.platform == 'win32':
            launcher_section = {
                'title': 'Launcher',
                'items': [
                    {'type': 'text', 'content': 'Rockstar Games Launcher is not installed.'},
                    {'type': 'button', 'label': 'Download Rockstar Games Launcher', 'action': {
                        'type': 'open_url', 'url': 'https://www.rockstargames.com/downloads',
                    }},
                ],
            }
        else:
            items = [
                {'type': 'text', 'content':
                    'Rockstar has no ownership/launch API — the launcher itself is the only source '
                    'of truth for what you own and how to install/launch it. Set the Wine binary and '
                    'prefix below, then use Configure Launcher above to install it.'},
                {'type': 'launcher_config'},
            ]
            _prefix, _wine_bin, _exe = _find_wine_launcher_config()
            if _exe:
                items.append({'type': 'text', 'content':
                              'Rockstar Games Launcher is installed. Use "Start Launcher" to open it '
                              'and sign in through the real app.'})
                items.append({'type': 'button', 'label': 'Start Launcher', 'action': {
                    'type': 'call', 'fn': 'rockstarStartLauncher',
                }})
                items.append({'type': 'button', 'label': 'Open Folder', 'action': {
                    'type': 'call', 'fn': 'rockstarOpenFolder',
                }})
                items.append({'type': 'status_output', 'key': 'folder'})
                items.append({'type': 'text', 'content':
                              'Once signed in, PlayDate can read your owned titles straight from the '
                              "launcher's own log files -- no PlayDate account connection needed."})
                items.append({'type': 'button', 'label': 'Sync Library from Launcher', 'action': {
                    'type': 'call', 'fn': 'rockstarSyncLocal',
                }})
                items.append({'type': 'status_output', 'key': 'sync_local'})
            launcher_section = {'title': 'Launcher', 'items': items}

        return {
            'sections': [
                {
                    'title': 'Account',
                    'auth': {
                        'endpoint': '/api/rockstar/status',
                        'disconnected': [
                            {'type': 'text', 'content':
                                'Account connection below is not yet working — Rockstar has no public '
                                'ownership API, and this flow is being replaced. Install the launcher below '
                                'and sign in through the real app instead.'},
                            {'type': 'button', 'label': 'Connect Rockstar Account', 'action': {
                                'type': 'oauth_popup',
                                'title': 'Connect Rockstar Social Club',
                                'url_endpoint': '/api/rockstar/auth-url',
                                'callback_endpoint': '/api/rockstar/connect',
                                'redirect_pattern': 'rockstargames.com',
                                'cookie_name': 'sc-auth-token',
                                'code_js': '',
                                'instructions': [
                                    'Sign in to your Rockstar Social Club account in the popup.',
                                    'PlayDate will connect automatically once login is complete.',
                                    'If the popup fails: sign in at '
                                    '<a href="https://www.rockstargames.com/" target="_blank">rockstargames.com</a> '
                                    'in your regular browser, open DevTools (F12) → Application → Cookies → rockstargames.com, '
                                    'copy the value of <code>sc-auth-token</code>, and paste it below.',
                                ],
                                'input_placeholder': 'Paste your sc-auth-token cookie value here…',
                                'open_label': 'Open Rockstar Sign In',
                                'submit_label': 'Connect',
                            }},
                            {'type': 'button', 'label': 'Paste sc-auth-token manually', 'variant': 'muted', 'action': {
                                'type': 'oauth_paste',
                                'title': 'Connect Rockstar Social Club',
                                'url_endpoint': '/api/rockstar/auth-url',
                                'callback_endpoint': '/api/rockstar/connect',
                                'instructions': [],
                                'input_placeholder': '',
                                'open_label': '',
                                'submit_label': 'Connect',
                            }},
                        ],
                        'connected': [
                            {'type': 'connected_label'},
                            {'type': 'buttons', 'items': [
                                {'label': 'Sync Library',
                                 'action': {'type': 'call', 'fn': 'rockstarSync'}},
                                {'label': 'Disconnect', 'variant': 'muted',
                                 'action': {'type': 'post', 'endpoint': '/api/rockstar/disconnect',
                                            'on_success': 'refresh_auth'}},
                            ]},
                            {'type': 'status_output', 'key': 'main'},
                        ],
                    },
                },
                launcher_section,
            ],
        }

    def fragments(self):
        return {
            'tools_scripts': 'rockstar_tools_scripts.html',
        }


plugin = RockstarPlugin()
