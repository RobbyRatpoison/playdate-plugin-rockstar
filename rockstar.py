import logging
import os
import re
import threading
import time
from datetime import date

import requests

from config import BASE_DIR, load_config, _save_config_data
from database import get_db, next_negative_appid, update_game_data

VERTICAL_DIR   = os.path.join(BASE_DIR, 'static', 'img', 'library', 'vertical')
HORIZONTAL_DIR = os.path.join(BASE_DIR, 'static', 'img', 'library', 'horizontal')

log = logging.getLogger(__name__)

SC_API = 'https://api.rockstargames.com'

# Rockstar's own public catalog -- titleId slug -> {friendlyName, rosTitleId,
# principalFileName, installFolderRegKey, ...}. Confirmed live 2026-09-04:
# still served, current, and far more trustworthy than the _TITLE_NAMES/
# _STORE_SLUGS guesses below (those two dicts' rosTitleId numbering doesn't
# even agree with this catalog's real numbering -- e.g. the old dict has
# '18' -> gta5's slug, but this catalog has gta5=rosTitleId 11 and
# gtasa=rosTitleId 18 -- so they're kept only as a last-resort fallback for
# a titleId this catalog doesn't (yet) list.
TITLE_METADATA_URL = 'https://gamedownloads-rockstargames-com.akamaized.net/public/title_metadata.json'
_title_metadata_cache = None
_title_metadata_lock  = threading.Lock()

# Fallback map of Rockstar title IDs to display names, used when the API
# does not return a name for a title.
_TITLE_NAMES = {
    '3':  'Grand Theft Auto III – The Definitive Edition',
    '7':  'Grand Theft Auto: Vice City – The Definitive Edition',
    '11': 'Grand Theft Auto: San Andreas – The Definitive Edition',
    '13': 'Grand Theft Auto IV',
    '16': 'Max Payne',
    '17': 'Max Payne 2: The Fall of Max Payne',
    '18': 'Grand Theft Auto V',
    '24': 'Max Payne 3',
    '26': 'Midnight Club: Los Angeles',
    '29': 'GTA Online',
    '31': 'Grand Theft Auto: San Andreas',
    '35': 'L.A. Noire',
    '44': 'Bully: Scholarship Edition',
    '45': 'Manhunt',
    '51': 'Grand Theft Auto: The Trilogy – The Definitive Edition',
    '56': 'Red Dead Online',
    '57': 'Red Dead Redemption 2',
    '58': 'Grand Theft Auto VI',
}

_STORE_SLUGS = {
    '3':  'grand-theft-auto-iii-the-definitive-edition',
    '7':  'grand-theft-auto-vice-city-the-definitive-edition',
    '11': 'grand-theft-auto-san-andreas-the-definitive-edition',
    '13': 'grand-theft-auto-iv-complete-edition',
    '18': 'grand-theft-auto-v',
    '24': 'max-payne-3',
    '35': 'l-a-noire',
    '44': 'bully-scholarship-edition',
    '51': 'grand-theft-auto-the-trilogy-the-definitive-edition',
    '57': 'red-dead-redemption-2',
}

_sync_state = {'running': False, 'status': '', 'added': 0, 'updated': 0, 'error': None}
_sync_lock  = threading.Lock()


# ── Config ──────────────────────────────────────────────────────────────────────

def _cfg():
    return (load_config() or {}).get('rockstar', {})

def _save_cfg(data):
    cfg = load_config() or {}
    cfg['rockstar'] = data
    _save_config_data(cfg)

def is_connected():
    return bool(_cfg().get('auth_token'))

def get_username():
    return _cfg().get('username', 'Connected')


# ── Auth ────────────────────────────────────────────────────────────────────────

def _headers(token=None):
    t = token or _cfg().get('auth_token', '')
    return {
        'Authorization': f'Bearer {t}',
        'x-requested-with': 'XMLHttpRequest',
        'User-Agent': 'Mozilla/5.0 (compatible; PlayDate)',
        'Accept': 'application/json',
    }


def connect(token):
    token = token.strip().strip('"')
    try:
        resp = requests.get(
            f'{SC_API}/user/profile',
            headers=_headers(token),
            timeout=15,
        )
        if not resp.ok:
            return False, f'Could not connect to Rockstar Social Club (HTTP {resp.status_code}) — check your sc-auth-token.'
        data = resp.json()
        log.debug(f'Rockstar profile response keys: {list(data.keys())}')
    except Exception as e:
        return False, f'Connection failed: {e}'

    accounts = data.get('accounts') or []
    if accounts:
        acct     = accounts[0]
        username = ((acct.get('rockstarAccount') or {}).get('nickname')
                    or acct.get('displayName') or 'Connected')
    else:
        username = data.get('nickname') or data.get('displayName') or 'Connected'

    _save_cfg({'auth_token': token, 'username': username})
    log.info(f'Rockstar connected as {username!r}')
    return True, username


def disconnect():
    cfg = load_config() or {}
    cfg.pop('rockstar', None)
    _save_config_data(cfg)


def _title_metadata():
    """Fetch and cache Rockstar's own public title catalog. Static data --
    fetched once per process lifetime, not per-user, so a plain in-memory
    cache is enough."""
    global _title_metadata_cache
    with _title_metadata_lock:
        if _title_metadata_cache is not None:
            return _title_metadata_cache
        try:
            resp = requests.get(TITLE_METADATA_URL, timeout=15)
            resp.raise_for_status()
            titles = resp.json().get('titles', {})
            _title_metadata_cache = {v['titleId']: v for v in titles.values() if v.get('titleId')}
        except Exception as e:
            log.warning(f'Rockstar: failed to fetch title_metadata.json: {e}')
            _title_metadata_cache = {}
        return _title_metadata_cache


def _slugify(name):
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', name.lower())).strip('-')


_BUILD_LINE_RE = re.compile(r'\[mergedmanifest\]\s+Using (?:build collection|regular build) for (\S+)')


def _parse_owned_titles_from_log_dir(log_dir):
    """Scan every rotated launcher*.log (launcher.log, launcher.01.log, ...)
    for '[mergedmanifest] Using build collection|regular build for <titleId>'
    lines. Confirmed live 2026-09-04 against a real signed-in launcher: this
    step only ever runs for a title the account's GetApps response reported a
    branch for (a fresh log showed 'GetApps: 23 title(s), 2 branch(es)'
    followed by exactly 2 mergedmanifest lines -- 'launcher' itself plus the
    one owned game) -- so presence alone is a clean, purely positive
    ownership signal. This replaces the old Galaxy-Plugin-Rockstar reference
    approach (byte-offset 'on branch '/'no branches!' parsing of a different,
    older log format) which no longer matches what the current launcher
    writes at all -- grepped for both phrases in a real fresh log and found
    neither."""
    owned = set()
    if not os.path.isdir(log_dir):
        return owned
    for fname in os.listdir(log_dir):
        if not fname.startswith('launcher') or not fname.endswith('.log'):
            continue
        try:
            with open(os.path.join(log_dir, fname), 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    m = _BUILD_LINE_RE.search(line)
                    if m and m.group(1) != 'launcher':
                        owned.add(m.group(1))
        except OSError:
            continue
    return owned


def _launcher_log_dir():
    """Documents/Rockstar Games/Launcher inside the configured Wine prefix, or None."""
    cfg = (load_config() or {}).get('launchers', {}).get('rockstar', {})
    prefix = cfg.get('prefix', '').strip()
    if not prefix or not os.path.isdir(prefix):
        return None
    from runners.wine import wine_user_dir
    user_dir = wine_user_dir(prefix)
    if not user_dir:
        return None
    log_dir = os.path.join(user_dir, 'Documents', 'Rockstar Games', 'Launcher')
    return log_dir if os.path.isdir(log_dir) else None


# ── Library sync ────────────────────────────────────────────────────────────────

def get_sync_state():
    return dict(_sync_state)


def start_local_library_sync():
    """Sync owned titles by parsing the real, signed-in Rockstar Games
    Launcher's own log files -- see _parse_owned_titles_from_log_dir(). No
    account connection needed on PlayDate's side; the launcher itself has
    already done that."""
    with _sync_lock:
        if _sync_state['running']:
            return {'status': 'already_running'}
        _sync_state.update({
            'running': True, 'status': 'Reading launcher logs…',
            'added': 0, 'updated': 0, 'error': None,
        })
    threading.Thread(target=_run_local_sync, daemon=True).start()
    return {'status': 'started'}


def _run_local_sync():
    try:
        _do_local_sync()
    except Exception as e:
        log.error(f'Rockstar local sync error: {e}', exc_info=True)
        _sync_state.update({'running': False, 'status': '', 'error': str(e)})


def _do_local_sync():
    log_dir = _launcher_log_dir()
    if not log_dir:
        raise RuntimeError('Rockstar Games Launcher not found — configure it in Plugins settings first.')

    owned = _parse_owned_titles_from_log_dir(log_dir)
    if not owned:
        _sync_state.update({
            'running': False,
            'status': 'No owned titles found in the launcher logs. Make sure you are signed in '
                      'and have opened My Library in the Rockstar Games Launcher, then try again.',
            'added': 0, 'updated': 0,
        })
        return

    metadata = _title_metadata()
    db = get_db()
    existing = {
        row['platform_id']: row['appid']
        for row in db.execute("SELECT appid, platform_id FROM games WHERE platform='rockstar'").fetchall()
    }
    blacklisted = {
        row[0] for row in db.execute(
            "SELECT platform_id FROM blacklist WHERE platform_id IS NOT NULL").fetchall()
    }

    added   = 0
    updated = 0
    for title_id in sorted(owned):
        if title_id in blacklisted:
            continue
        meta = metadata.get(title_id, {})
        name = meta.get('friendlyName') or _TITLE_NAMES.get(title_id, title_id)
        _sync_state['status'] = f'Processing {name}…'

        if title_id in existing:
            updated += 1
            continue

        appid = next_negative_appid(db)
        db.execute(
            """INSERT OR IGNORE INTO games
               (appid, name, platform, platform_id, platform_slug,
                date_added, completion_status, installed,
                art_fetched, meta_fetched, cheevos_fetched,
                protondb_fetched, hltb_fetched)
               VALUES (?, ?, 'rockstar', ?, ?,
                       ?, 'Never Played', 0,
                       '0', ?, '0', '0', '0')""",
            (appid, name, title_id, _slugify(name), int(time.time()), date.today().isoformat()),
        )
        db.commit()
        existing[title_id] = appid
        added += 1
        log.info(f'Rockstar local sync: added {name!r} (titleId={title_id})')
        _fetch_art(appid, name)

    db.close()
    _sync_state.update({
        'running': False,
        'status': f'Done — {added} added, {updated} already in library.',
        'added': added, 'updated': updated,
    })
    log.info(f'Rockstar local sync complete: {added} added, {updated} existing, {len(owned)} owned total')


def start_library_sync():
    with _sync_lock:
        if _sync_state['running']:
            return {'status': 'already_running'}
        _sync_state.update({
            'running': True, 'status': 'Starting…',
            'added': 0, 'updated': 0, 'error': None,
        })
    threading.Thread(target=_run_sync, daemon=True).start()
    return {'status': 'started'}


def _run_sync():
    global _sync_state
    try:
        _sync_state['status'] = 'Fetching library…'
        resp = requests.get(
            f'{SC_API}/user/profile',
            params={'includeTitleInfo': 'true'},
            headers=_headers(),
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError(f'API error (HTTP {resp.status_code}) — try reconnecting.')
        data = resp.json()
        log.debug(f'Rockstar profile/titleInfo response keys: {list(data.keys())}')

        # The API may nest title info under different keys depending on endpoint version.
        # Try several locations and log what we find for debugging.
        title_list = (
            data.get('titleInfo')
            or data.get('titleStates')
            or data.get('titles')
            or []
        )

        if not title_list:
            log.warning(f'Rockstar sync: no title list found. Top-level keys: {list(data.keys())}')
            raise RuntimeError(
                'No game titles returned. The API may require a different endpoint — '
                'check playdate.log for the response structure.'
            )

        log.info(f'Rockstar: {len(title_list)} titles in response')

        db = get_db()
        existing = {
            row['platform_id']: row['appid']
            for row in db.execute(
                "SELECT appid, platform_id FROM games WHERE platform='rockstar'"
            ).fetchall()
        }
        blacklisted = {
            row[0]
            for row in db.execute(
                "SELECT platform_id FROM blacklist WHERE platform_id IS NOT NULL"
            ).fetchall()
        }

        added   = 0
        updated = 0

        for title in title_list:
            # Skip titles the user doesn't own (some endpoints include all titles)
            if title.get('owned') is False:
                continue

            title_id = str(title.get('titleId') or title.get('id') or '').strip()
            if not title_id:
                continue

            name = (
                title.get('titleName')
                or title.get('name')
                or _TITLE_NAMES.get(title_id)
                or f'Rockstar Title {title_id}'
            )
            slug = (
                title.get('slug')
                or title.get('titleSlug')
                or _STORE_SLUGS.get(title_id)
                or ''
            )

            _sync_state['status'] = f'Processing {name}…'

            if title_id in blacklisted:
                continue

            if title_id in existing:
                updated += 1
            else:
                appid = next_negative_appid(db)
                db.execute(
                    """INSERT OR IGNORE INTO games
                       (appid, name, platform, platform_id, platform_slug,
                        date_added, completion_status, installed,
                        art_fetched, meta_fetched, cheevos_fetched,
                        protondb_fetched, hltb_fetched)
                       VALUES (?, ?, 'rockstar', ?, ?,
                               ?, 'Never Played', 0,
                               '0', ?, '0', '0', '0')""",
                    (appid, name, title_id, slug, int(time.time()), date.today().isoformat()),
                )
                db.commit()
                existing[title_id] = appid
                added += 1
                log.info(f'Rockstar sync: added {name!r} (titleId={title_id})')
                _fetch_art(appid, name)

        db.close()
        _sync_state.update({
            'running': False,
            'status': f'Done — {added} added, {updated} already in library.',
            'added': added, 'updated': updated,
        })
        log.info(f'Rockstar sync complete: {added} added, {updated} existing')

    except Exception as e:
        log.error(f'Rockstar sync error: {e}', exc_info=True)
        _sync_state.update({'running': False, 'status': '', 'error': str(e)})


# ── Art ─────────────────────────────────────────────────────────────────────────

def _fetch_art(appid, name):
    try:
        from images import _sgdb_search_game_id, download_vertical, download_horizontal
        sgdb_id = _sgdb_search_game_id(name)
        vert    = download_vertical(appid, sgdb_id=sgdb_id, game_name=name)
        horiz   = download_horizontal(appid, sgdb_id=sgdb_id, game_name=name)
        if vert != 'missing' or horiz != 'missing':
            update_game_data(appid, art_fetched=date.today().isoformat())
    except Exception as e:
        log.warning(f'Rockstar: art fetch failed for {name!r}: {e}')


# ── Single-game rescrape ──────────────────────────────────────────────────────

def scrape_single(appid):
    """Re-fetch art for one Rockstar game. Returns a meta dict or None."""
    db  = get_db()
    row = db.execute(
        "SELECT platform_id, name FROM games WHERE appid = ? AND platform = 'rockstar'",
        (appid,)
    ).fetchone()
    db.close()
    if not row:
        return None
    _fetch_art(appid, row['name'] or f'Rockstar {row["platform_id"]}')
    return {'meta_fetched': date.today().isoformat()}


# ── Install / launch / uninstall ─────────────────────────────────────────────────
#
# Rockstar has no launch protocol URL and install/uninstall is Windows-
# registry-only in the real reference client (Galaxy-Plugin-Rockstar's
# local.py) -- but the Launcher.exe CLI flags it shells out to
# (`-enableFullMode -install=<titleId>` / `-uninstall=<titleId>`) work fine
# invoked directly under Wine, no registry lookup needed since we already
# know the exe's path from the Configure Launcher install. Actual
# download/install progress is entirely the real Launcher's own GUI window
# (visible to the user in the Wine session) -- PlayDate has no visibility
# into it and doesn't try to duplicate a progress bar, same precedent as the
# Battle.net plugin's protocol-only install trigger.

def _find_game_exe(prefix, exe_name):
    """Search the prefix's Program Files trees for exe_name (title_metadata's
    principalFileName -- sometimes a subpath like 'Gameface\\Binaries\\
    Win64\\LibertyCity.exe' for the Unreal remasters, so match on basename)."""
    if not exe_name or not prefix:
        return None
    basename = exe_name.replace('\\', '/').rsplit('/', 1)[-1]
    for pf in ('Program Files', 'Program Files (x86)'):
        root = os.path.join(prefix, 'drive_c', pf)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            if basename in files:
                return os.path.join(dirpath, basename)
    return None


def _launcher_config():
    """(prefix, wine_bin, launcher_exe_path), or (None, None, None)."""
    from . import _find_wine_launcher_config
    return _find_wine_launcher_config()


def launch_game(appid):
    db  = get_db()
    row = db.execute("SELECT name, platform_id, installed FROM games WHERE appid=?", (appid,)).fetchone()
    db.close()
    if not row:
        return {'status': 'error', 'message': 'Rockstar game not found'}

    name     = row['name']
    title_id = (row['platform_id'] or '').strip()
    if not title_id:
        return {'status': 'error', 'message': 'Game has no title ID — try re-syncing'}

    prefix, wine_bin, launcher_exe = _launcher_config()
    if not launcher_exe:
        return {'status': 'error', 'message': 'Rockstar Games Launcher not found. Check Wine setup in Plugins settings.'}

    meta     = _title_metadata().get(title_id, {})
    exe_name = meta.get('principalFileName')
    game_exe = _find_game_exe(prefix, exe_name) if exe_name else None

    try:
        from runners.wine import run_in_prefix
        if game_exe:
            # Match the real Launcher's own command line exactly (confirmed
            # live 2026-09-04 from launcher.log's [gamelaunch] entry) --
            # -fromRGL/-rglLanguage may affect a title's own behavior even
            # though they don't matter for basic process startup.
            run_in_prefix(prefix, game_exe, args=['-fromRGL', '-rglLanguage=en-US'],
                          wine_bin=wine_bin, env_extra={'WINEDEBUG': '-all'})
            now = int(time.time())
            update_game_data(appid, installed=1, install_path=os.path.dirname(game_exe), last_played=now)
            return {'status': 'success', 'last_played': now}

        # Not installed yet -- trigger the real install through the Launcher
        # itself (its own GUI shows progress; we just poke it to start).
        run_in_prefix(prefix, launcher_exe, args=['-enableFullMode', f'-install={title_id}'],
                      wine_bin=wine_bin, env_extra={'WINEDEBUG': '-all'})
    except RuntimeError as e:
        return {'status': 'error', 'message': str(e)}
    except Exception as e:
        return {'status': 'error', 'message': f'Launch failed: {e}'}

    return {
        'status':  'installing',
        'message': f'Opening Rockstar Games Launcher to install {name}…',
    }


def uninstall_game(appid):
    db  = get_db()
    row = db.execute("SELECT platform_id FROM games WHERE appid=?", (appid,)).fetchone()
    db.close()
    if not row:
        return {'status': 'error', 'message': 'Rockstar game not found'}

    title_id = (row['platform_id'] or '').strip()
    if not title_id:
        return {'status': 'error', 'message': 'Game has no title ID'}

    prefix, wine_bin, launcher_exe = _launcher_config()
    if not launcher_exe:
        return {'status': 'error', 'message': 'Rockstar Games Launcher not found.'}

    try:
        from runners.wine import run_in_prefix
        run_in_prefix(prefix, launcher_exe, args=['-enableFullMode', f'-uninstall={title_id}'],
                      wine_bin=wine_bin, env_extra={'WINEDEBUG': '-all'})
    except RuntimeError as e:
        return {'status': 'error', 'message': str(e)}
    except Exception as e:
        return {'status': 'error', 'message': f'Uninstall failed: {e}'}
    return {'status': 'success'}


def resync_installed():
    """Re-check the installed flag/install_path for every Rockstar game
    against the configured Wine prefix. Called at plugin startup and after a
    backup restore -- picks up installs finished through the Launcher's own
    GUI that PlayDate never watched happen.

    A missing or absent prefix means nothing on this platform can be
    installed, so stale installed=1 rows get cleared too (e.g. the prefix was
    deleted after a game was installed -- otherwise the "Installed" badge and
    Open Folder disagree forever, since nothing else downgrades the flag).
    Read the prefix straight from config rather than via _launcher_config(),
    which also returns nothing when Launcher.exe can't be found -- here we
    only care whether the prefix directory itself exists."""
    cfg    = (load_config() or {}).get('launchers', {}).get('rockstar', {})
    prefix = (cfg.get('prefix') or '').strip()
    have_prefix = bool(prefix) and os.path.isdir(prefix)

    metadata = _title_metadata() if have_prefix else {}
    db = get_db()
    rows = db.execute(
        "SELECT appid, platform_id, installed FROM games WHERE platform='rockstar'"
    ).fetchall()
    for row in rows:
        exe = None
        if have_prefix:
            title_id = (row['platform_id'] or '').strip()
            exe_name = metadata.get(title_id, {}).get('principalFileName')
            exe      = _find_game_exe(prefix, exe_name) if exe_name else None
        now_installed = 1 if exe else 0
        if bool(row['installed']) != bool(now_installed):
            update_game_data(row['appid'], installed=now_installed,
                             install_path=(os.path.dirname(exe) if exe else ''))
    db.close()
