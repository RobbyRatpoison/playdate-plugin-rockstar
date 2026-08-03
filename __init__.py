import logging

log = logging.getLogger(__name__)


class RockstarPlugin:
    id       = 'rockstar'
    name     = 'Rockstar Games'
    platform = 'rockstar'
    label    = 'Rockstar Games'

    def register(self, app):
        from .routes import bp
        app.register_blueprint(bp)
        log.info('Rockstar Games plugin registered')

    def on_startup(self):
        pass

    def on_shutdown(self):
        pass

    def on_uninstall(self):
        from .rockstar import disconnect
        disconnect()

    def launch_game(self, appid):
        from .rockstar import launch_game
        return launch_game(appid)

    def rescrape(self, appid):
        from .rockstar import scrape_single
        return scrape_single(appid) or None

    def fetch_description(self, appid, platform_id):
        return None

    def js_api(self):
        return {
            'uninstall_url':  None,
            'scrape_url':     '/api/rockstar/rescrape/{appid}',
            'scrape_method':  'POST',
            'store_url':      'https://store.rockstargames.com/game/{slug}',
            'store_label':    'View on Rockstar Store ↗',
            'appid_label':    'Rockstar Title ID:',
            'sync_label':     'Sync Rockstar Library',
        }

    def manage_ui(self):
        return {
            'sections': [
                {
                    'title': 'Account',
                    'auth': {
                        'endpoint': '/api/rockstar/status',
                        'disconnected': [
                            {'type': 'text', 'content': 'Connect your Rockstar Games account to import your library.'},
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
            ],
        }

    def fragments(self):
        return {
            'tools_scripts': 'rockstar_tools_scripts.html',
        }


plugin = RockstarPlugin()
