# -*- coding: utf-8 -*-
import hashlib
import math
import os
import sys
import errno
import datetime
import re
import struct
import json
import logging
import shutil
import puremagic
import urllib
import base64

import pyhindsight.lib.ccl_chrome_indexeddb.ccl_blink_value_deserializer
from pyhindsight.browsers.webbrowser import WebBrowser
from pyhindsight import utils

# Try to import optionally modules - do nothing on failure, as status is tracked elsewhere
try:
    import win32crypt
except ImportError:
    pass

try:
    import keyring
except ImportError:
    pass

try:
    from Cryptodome.Cipher import AES
    from Cryptodome.Protocol.KDF import PBKDF2
except ImportError:
    pass

log = logging.getLogger(__name__)


class Chrome(WebBrowser):
    def __init__(self, profile_path, browser_name=None, cache_path=None, version=None, timezone=None,
                 parsed_artifacts=None, parsed_storage=None, storage=None, installed_extensions=None,
                 artifacts_counts=None, artifacts_display=None, available_decrypts=None, preferences=None,
                 no_copy=None, temp_dir=None, origin_hashes=None, hsts_hashes=None):
        WebBrowser.__init__(
            self, profile_path, browser_name=browser_name, cache_path=cache_path, version=version, timezone=timezone,
            parsed_artifacts=parsed_artifacts, parsed_storage=parsed_storage, artifacts_counts=artifacts_counts,
            artifacts_display=artifacts_display, preferences=preferences, no_copy=no_copy, temp_dir=temp_dir,
            origin_hashes=origin_hashes)
        self.profile_path = profile_path
        self.browser_name = "Chrome"
        self.cache_path = cache_path
        self.timezone = timezone
        self.installed_extensions = installed_extensions
        self.cached_key = None
        self.available_decrypts = available_decrypts
        self.storage = storage
        self.preferences = preferences
        self.no_copy = no_copy
        self.temp_dir = temp_dir
        self.origin_hashes = origin_hashes
        self.hsts_hashes = hsts_hashes

        if self.version is None:
            self.version = []

        if self.structure is None:
            self.structure = {}

        if self.parsed_artifacts is None:
            self.parsed_artifacts = []

        if self.parsed_storage is None:
            self.parsed_storage = []

        if self.installed_extensions is None:
            self.installed_extensions = []

        if self.preferences is None:
            self.preferences = []

        if self.origin_hashes is None:
            self.origin_hashes = {}

        if self.hsts_hashes is None:
            self.hsts_hashes = {}

        if self.artifacts_counts is None:
            self.artifacts_counts = {}

        if self.storage is None:
            self.storage = {}

        if self.artifacts_display is None:
            self.artifacts_display = {}

        if self.available_decrypts is None:
            self.available_decrypts = {'windows': 0, 'mac': 0, 'linux': 0}

        if self.available_decrypts['windows'] == 1:
            import win32crypt

        if self.available_decrypts['mac'] == 1:
            import keyring
            from Cryptodome.Cipher import AES
            from Cryptodome.Protocol.KDF import PBKDF2

        if self.available_decrypts['linux'] == 1:
            from Cryptodome.Cipher import AES
            from Cryptodome.Protocol.KDF import PBKDF2

    def determine_version(self):
        """Determine version of Chrome databases files by looking for combinations of columns in certain tables.
        Based on research I did to create "Chrome Evolution" tool - dfir.blog/chrome-evolution
        """

        possible_versions = list(range(1, 101))
        # TODO: remove 82?
        previous_possible_versions = possible_versions[:]

        def update_and_rollback_if_empty(version_list, prev_version_list):
            if len(version_list) == 0:
                version_list = prev_version_list
                log.warning('Last version structure check eliminated all possible versions; skipping that file.')
            else:
                prev_version_list = version_list[:]
            return version_list, prev_version_list

        def trim_lesser_versions_if(column, table, version):
            """Remove version numbers < 'version' from 'possible_versions' if 'column' isn't in 'table', and keep
            versions >= 'version' if 'column' is in 'table'.
            """
            if table:
                if column in table:
                    possible_versions[:] = [x for x in possible_versions if x >= version]
                else:
                    possible_versions[:] = [x for x in possible_versions if x < version]

        def trim_greater_versions_if(column, table, version):
            """Remove version numbers > 'version' from 'possible_versions' if 'column' isn't in 'table', and keep
            versions =< 'version' if 'column' is in 'table'.
            """
            if table:
                if column in table:
                    possible_versions[:] = [x for x in possible_versions if x <= version]
                else:
                    possible_versions[:] = [x for x in possible_versions if x > version]

        def trim_lesser_versions(version):
            """Remove version numbers < 'version' from 'possible_versions'"""
            possible_versions[:] = [x for x in possible_versions if x >= version]

        if 'History' in list(self.structure.keys()):
            log.debug('Analyzing \'History\' structure')
            log.debug(f' - Starting possible versions:  {possible_versions}')
            if 'visits' in list(self.structure['History'].keys()):
                trim_lesser_versions_if('visit_duration', self.structure['History']['visits'], 20)
                trim_lesser_versions_if('incremented_omnibox_typed_score', self.structure['History']['visits'], 68)
            if 'visit_source' in list(self.structure['History'].keys()):
                trim_lesser_versions_if('source', self.structure['History']['visit_source'], 7)
            if 'downloads' in list(self.structure['History'].keys()):
                trim_lesser_versions_if('target_path', self.structure['History']['downloads'], 26)
                trim_lesser_versions_if('opened', self.structure['History']['downloads'], 16)
                trim_lesser_versions_if('etag', self.structure['History']['downloads'], 30)
                trim_lesser_versions_if('original_mime_type', self.structure['History']['downloads'], 37)
                trim_lesser_versions_if('last_access_time', self.structure['History']['downloads'], 59)
            if 'downloads_slices' in list(self.structure['History'].keys()):
                trim_lesser_versions(58)
            if 'content_annotations' in list(self.structure['History'].keys()):
                trim_lesser_versions(91)
                trim_lesser_versions_if('visibility_score', self.structure['History']['content_annotations'], 95)
                trim_lesser_versions_if('search_terms', self.structure['History']['content_annotations'], 100)
            if 'context_annotations' in list(self.structure['History'].keys()):
                trim_lesser_versions(92)
                trim_lesser_versions_if(
                    'total_foreground_duration', self.structure['History']['context_annotations'], 96)
            if 'clusters' in list(self.structure['History'].keys()):
                trim_lesser_versions(93)
            if 'downloads_reroute_info' in list(self.structure['History'].keys()):
                trim_lesser_versions(94)
            log.debug(f' - Finishing possible versions: {possible_versions}')

        # the pseudo-History file generated by the ChromeNative Volatility plugin should use the v30 query
        elif (db.startswith('History__') for db in list(self.structure.keys())):
            trim_lesser_versions(30)

        possible_versions, previous_possible_versions = \
            update_and_rollback_if_empty(possible_versions, previous_possible_versions)

        if 'Cookies' in list(self.structure.keys()):
            log.debug("Analyzing 'Cookies' structure")
            log.debug(f' - Starting possible versions:  {possible_versions}')
            if 'cookies' in list(self.structure['Cookies'].keys()):
                trim_lesser_versions_if('is_same_party', self.structure['Cookies']['cookies'], 88)
                trim_lesser_versions_if('source_scheme', self.structure['Cookies']['cookies'], 80)
                trim_lesser_versions_if('samesite', self.structure['Cookies']['cookies'], 76)
                trim_lesser_versions_if('is_persistent', self.structure['Cookies']['cookies'], 66)
                trim_lesser_versions_if('encrypted_value', self.structure['Cookies']['cookies'], 33)
                trim_lesser_versions_if('priority', self.structure['Cookies']['cookies'], 28)
            log.debug(f' - Finishing possible versions: {possible_versions}')

        possible_versions, previous_possible_versions = \
            update_and_rollback_if_empty(possible_versions, previous_possible_versions)

        if 'Web Data' in list(self.structure.keys()):
            log.debug("Analyzing 'Web Data' structure")
            log.debug(f' - Starting possible versions:  {possible_versions}')
            if 'autofill' in list(self.structure['Web Data'].keys()):
                trim_lesser_versions_if('name', self.structure['Web Data']['autofill'], 2)
                trim_lesser_versions_if('date_created', self.structure['Web Data']['autofill'], 35)
            if 'autofill_profiles' in list(self.structure['Web Data'].keys()):
                trim_lesser_versions_if('language_code', self.structure['Web Data']['autofill_profiles'], 36)
                trim_lesser_versions_if('validity_bitfield', self.structure['Web Data']['autofill_profiles'], 63)
                trim_lesser_versions_if(
                    'is_client_validity_states_updated', self.structure['Web Data']['autofill_profiles'], 71)
            if 'autofill_profile_addresses' in list(self.structure['Web Data'].keys()):
                trim_lesser_versions(86)
                trim_lesser_versions_if('city', self.structure['Web Data']['autofill_profile_addresses'], 87)
            if 'autofill_sync_metadata' in list(self.structure['Web Data'].keys()):
                trim_lesser_versions(57)
                trim_lesser_versions_if('model_type', self.structure['Web Data']['autofill_sync_metadata'], 69)
            if 'web_apps' not in list(self.structure['Web Data'].keys()):
                trim_lesser_versions(38)
            if 'credit_cards' in list(self.structure['Web Data'].keys()):
                trim_lesser_versions_if('billing_address_id', self.structure['Web Data']['credit_cards'], 53)
                trim_lesser_versions_if('nickname', self.structure['Web Data']['credit_cards'], 85)
            log.debug(f' - Finishing possible versions: {possible_versions}')

        possible_versions, previous_possible_versions = \
            update_and_rollback_if_empty(possible_versions, previous_possible_versions)

        if 'Login Data' in list(self.structure.keys()):
            log.debug("Analyzing 'Login Data' structure")
            log.debug(f' - Starting possible versions:  {possible_versions}')
            if 'logins' in list(self.structure['Login Data'].keys()):
                trim_lesser_versions_if('display_name', self.structure['Login Data']['logins'], 39)
                trim_lesser_versions_if('generation_upload_status', self.structure['Login Data']['logins'], 42)
                trim_greater_versions_if('ssl_valid', self.structure['Login Data']['logins'], 53)
                trim_lesser_versions_if('possible_username_pairs', self.structure['Login Data']['logins'], 59)
                trim_lesser_versions_if('id', self.structure['Login Data']['logins'], 73)
                trim_lesser_versions_if('moving_blocked_for', self.structure['Login Data']['logins'], 84)
            if 'field_info' in list(self.structure['Login Data'].keys()):
                trim_lesser_versions(80)
            if 'compromised_credentials' in list(self.structure['Login Data'].keys()):
                trim_lesser_versions(83)
            if 'insecure_credentials' in list(self.structure['Login Data'].keys()):
                trim_lesser_versions(89)
            log.debug(f' - Finishing possible versions: {possible_versions}')

        possible_versions, previous_possible_versions = \
            update_and_rollback_if_empty(possible_versions, previous_possible_versions)

        if 'Network Action Predictor' in list(self.structure.keys()):
            log.debug("Analyzing 'Network Action Predictor' structure")
            log.debug(f' - Starting possible versions:  {possible_versions}')
            if 'resource_prefetch_predictor_url' in list(self.structure['Network Action Predictor'].keys()):
                trim_lesser_versions(22)
                trim_lesser_versions_if(
                    'key', self.structure['Network Action Predictor']['resource_prefetch_predictor_url'], 55)
                trim_lesser_versions_if(
                    'proto', self.structure['Network Action Predictor']['resource_prefetch_predictor_url'], 54)
            log.debug(f' - Finishing possible versions: {possible_versions}')

        possible_versions, previous_possible_versions = \
            update_and_rollback_if_empty(possible_versions, previous_possible_versions)

        self.version = possible_versions

    def get_history(self, path, history_file, version, row_type):
        results = []

        log.info(f'History items from {history_file}')

        # Queries for different versions
        query = {59: '''SELECT urls.id, urls.url, urls.title, urls.visit_count, urls.typed_count, urls.last_visit_time,
                            urls.hidden, visits.visit_time, visits.from_visit, visits.visit_duration,
                            visits.transition, visit_source.source
                        FROM urls JOIN visits 
                        ON urls.id = visits.url LEFT JOIN visit_source ON visits.id = visit_source.id''',
                 30: '''SELECT urls.id, urls.url, urls.title, urls.visit_count, urls.typed_count, urls.last_visit_time,
                            urls.hidden, urls.favicon_id, visits.visit_time, visits.from_visit, visits.visit_duration,
                            visits.transition, visit_source.source
                        FROM urls JOIN visits 
                        ON urls.id = visits.url LEFT JOIN visit_source ON visits.id = visit_source.id''',
                 29: '''SELECT urls.id, urls.url, urls.title, urls.visit_count, urls.typed_count, urls.last_visit_time,
                            urls.hidden, urls.favicon_id, visits.visit_time, visits.from_visit, visits.visit_duration,
                            visits.transition, visit_source.source, visits.is_indexed
                        FROM urls JOIN visits 
                        ON urls.id = visits.url LEFT JOIN visit_source ON visits.id = visit_source.id''',
                 20: '''SELECT urls.id, urls.url, urls.title, urls.visit_count, urls.typed_count, urls.last_visit_time,
                            urls.hidden, urls.favicon_id, visits.visit_time, visits.from_visit, visits.visit_duration,
                            visits.transition, visit_source.source, visits.is_indexed
                        FROM urls JOIN visits 
                        ON urls.id = visits.url LEFT JOIN visit_source ON visits.id = visit_source.id''',
                 7:  '''SELECT urls.id, urls.url, urls.title, urls.visit_count, urls.typed_count, urls.last_visit_time,
                            urls.hidden, urls.favicon_id, visits.visit_time, visits.from_visit, visits.transition,
                            visit_source.source
                        FROM urls JOIN visits 
                        ON urls.id = visits.url LEFT JOIN visit_source ON visits.id = visit_source.id''',
                 1:  '''SELECT urls.id, urls.url, urls.title, urls.visit_count, urls.typed_count, urls.last_visit_time,
                            urls.hidden, urls.favicon_id, visits.visit_time, visits.from_visit, visits.transition
                        FROM urls, visits WHERE urls.id = visits.url'''}

        # Get the lowest possible version from the version list, and decrement it until it finds a matching query
        compatible_version = version[0]
        while compatible_version not in list(query.keys()) and compatible_version > 0:
            compatible_version -= 1

        if compatible_version != 0:
            log.info(f' - Using SQL query for History items for Chrome {compatible_version}')
            try:
                # Copy and connect to copy of 'History' SQLite DB
                conn = utils.open_sqlite_db(self, path, history_file)
                if not conn:
                    self.artifacts_counts[history_file] = 'Failed'
                    return
                cursor = conn.cursor()

                # Use highest compatible version SQL to select download data
                try:
                    cursor.execute(query[compatible_version])
                except Exception as e:
                    log.error(f' - Error querying {history_file}: {e}')
                    self.artifacts_counts[history_file] = 'Failed'
                    return

                for row in cursor:
                    duration = None
                    if row.get('visit_duration'):
                        duration = datetime.timedelta(microseconds=row.get('visit_duration'))

                    new_row = Chrome.URLItem(
                        self.profile_path, row.get('id'), row.get('url'), row.get('title'),
                        utils.to_datetime(row.get('visit_time'), self.timezone),
                        utils.to_datetime(row.get('last_visit_time'), self.timezone),
                        row.get('visit_count'), row.get('typed_count'), row.get('from_visit'),
                        row.get('transition'), row.get('hidden'), row.get('favicon_id'),
                        row.get('is_indexed'), str(duration), row.get('source'))

                    # Set the row type as determined earlier
                    new_row.row_type = row_type

                    # Translate the transition value to human-readable
                    new_row.decode_transition()

                    # Translate the numeric visit_source.source code to human-readable
                    new_row.decode_source()

                    # Add the new row to the results array
                    results.append(new_row)

                conn.close()

                self.artifacts_counts[history_file] = len(results)
                log.info(f' - Parsed {len(results)} items')
                self.parsed_artifacts.extend(results)

            except Exception as e:
                self.artifacts_counts[history_file] = 'Failed'
                log.error(f' - Exception parsing {os.path.join(path, history_file)}; {e}')

    def get_media_history(self, path, history_file, version, row_type):
        results = []

        log.info(f'Media History items from {history_file}')

        # Queries for different versions
        query = {86: '''SELECT playback.url, playback.last_updated_time_s, playback.watch_time_s,
                            playback.has_video, playback.has_audio, playbackSession.title, 
                            playbackSession.source_title, playbackSession.duration_ms, playbackSession.position_ms
                        FROM playback LEFT JOIN playbackSession 
                            ON playback.last_updated_time_s = playbackSession.last_updated_time_s'''}

        # Get the lowest possible version from the version list, and decrement it until it finds a matching query
        compatible_version = version[0]
        while compatible_version not in list(query.keys()) and compatible_version > 0:
            compatible_version -= 1

        if compatible_version != 0:
            log.info(f' - Using SQL query for Media History items for Chrome {compatible_version}')
            try:
                # Copy and connect to copy of 'Media History' SQLite DB
                conn = utils.open_sqlite_db(self, path, history_file)
                if not conn:
                    self.artifacts_counts[history_file] = 'Failed'
                    return
                cursor = conn.cursor()

                # Use highest compatible version SQL to select download data
                try:
                    cursor.execute(query[compatible_version])
                except Exception as e:
                    log.error(f" - Error querying '{history_file}': {e}")
                    self.artifacts_counts[history_file] = 'Failed'
                    return

                for row in cursor:
                    duration = None
                    if row.get('duration_ms'):
                        # Check is duration value is reasonable; some have been equivalent of 300 million years
                        if row.get('duration_ms') < 2600000:
                            duration = str(datetime.timedelta(milliseconds=row.get('duration_ms')))[:-3]

                    position = None
                    if row.get('position_ms'):
                        position = str(datetime.timedelta(milliseconds=row.get('position_ms')))[:-3]

                    watch_time = ' 0:00:00'
                    if row.get('watch_time_s'):
                        watch_time = ' ' + str(datetime.timedelta(seconds=row.get('watch_time_s')))

                    row_title = ''
                    if row.get('title'):
                        row_title = row.get('title')

                    new_row = Chrome.MediaItem(
                        self.profile_path, row.get('url'), row_title,
                        utils.to_datetime(row.get('last_updated_time_s'), self.timezone), position,
                        duration, row.get('source_title'), watch_time, row.get('has_video'), row.get('has_audio'))

                    # Set the row type as determined earlier
                    new_row.row_type = row_type

                    # Add the new row to the results array
                    results.append(new_row)

                conn.close()

                self.artifacts_counts[history_file] = len(results)
                log.info(f' - Parsed {len(results)} items')
                self.parsed_artifacts.extend(results)

            except Exception as e:
                self.artifacts_counts[history_file] = 'Failed'
                log.error(f' - Exception parsing {os.path.join(path, history_file)}; {e}')

    def get_downloads(self, path, database, version, row_type):
        # Set up empty return array
        results = []

        log.info(f'Download items from {database}:')

        # Queries for different versions
        query = {30: '''SELECT downloads.id, downloads_url_chains.url, downloads.received_bytes, downloads.total_bytes,
                            downloads.state, downloads.target_path, downloads.start_time, downloads.end_time,
                            downloads.opened, downloads.danger_type, downloads.interrupt_reason, downloads.etag,
                            downloads.last_modified, downloads_url_chains.chain_index
                        FROM downloads, downloads_url_chains WHERE downloads_url_chains.id = downloads.id''',
                 26: '''SELECT downloads.id, downloads_url_chains.url, downloads.received_bytes, downloads.total_bytes,
                            downloads.state, downloads.target_path, downloads.start_time, downloads.end_time,
                            downloads.opened, downloads.danger_type, downloads.interrupt_reason,
                            downloads_url_chains.chain_index
                        FROM downloads, downloads_url_chains WHERE downloads_url_chains.id = downloads.id''',
                 16: '''SELECT downloads.id, downloads.url, downloads.received_bytes, downloads.total_bytes,
                            downloads.state, downloads.full_path, downloads.start_time, downloads.end_time,
                            downloads.opened
                        FROM downloads''',
                 1:  '''SELECT downloads.id, downloads.url, downloads.received_bytes, downloads.total_bytes,
                            downloads.state, downloads.full_path, downloads.start_time
                        FROM downloads'''}

        # Get the lowest possible version from the version list, and decrement it until it finds a matching query
        compatible_version = version[0]
        while compatible_version not in list(query.keys()) and compatible_version > 0:
            compatible_version -= 1

        if compatible_version != 0:
            log.info(f' - Using SQL query for Download items for Chrome v{compatible_version}')
            try:
                # Copy and connect to copy of 'History' SQLite DB
                conn = utils.open_sqlite_db(self, path, database)
                if not conn:
                    self.artifacts_counts[database + '_downloads'] = 'Failed'
                    return
                cursor = conn.cursor()

                # Use highest compatible version SQL to select download data
                cursor.execute(query[compatible_version])

                for row in cursor:
                    try:
                        # TODO: collapse download chain into one entry per download
                        new_row = Chrome.DownloadItem(
                            self.profile_path, row.get('id'), row.get('url'), row.get('received_bytes'),
                            row.get('total_bytes'), row.get('state'), row.get('full_path'),
                            utils.to_datetime(row.get('start_time'), self.timezone),
                            utils.to_datetime(row.get('end_time'), self.timezone), row.get('target_path'),
                            row.get('current_path'), row.get('opened'), row.get('danger_type'),
                            row.get('interrupt_reason'), row.get('etag'), row.get('last_modified'),
                            row.get('chain_index'))
                    except:
                        log.exception(' - Exception processing record; skipped.')
                        continue

                    new_row.decode_interrupt_reason()
                    new_row.decode_danger_type()
                    new_row.decode_download_state()
                    new_row.timestamp = new_row.start_time

                    new_row.create_friendly_status()

                    if new_row.full_path is not None:
                        new_row.value = new_row.full_path
                    elif new_row.current_path is not None:
                        new_row.value = new_row.current_path
                    elif new_row.target_path is not None:
                        new_row.value = new_row.target_path
                    else:
                        new_row.value = 'Error retrieving download location'
                        log.error(f' - Error retrieving download location for download "{new_row.url}"')

                    new_row.row_type = row_type
                    results.append(new_row)

                conn.close()

                self.artifacts_counts[database + '_downloads'] = len(results)
                log.info(f' - Parsed {len(results)} items')
                self.parsed_artifacts.extend(results)

            except IOError:
                self.artifacts_counts[database + '_downloads'] = 'Failed'
                log.error(f' - Couldn\'t open {os.path.join(path, database)}')

    def decrypt_cookie(self, encrypted_value):
        """Decryption based on work by Nathan Henrie and Jordan Wright as well as Chromium source:
         - Mac/Linux: http://n8henrie.com/2014/05/decrypt-chrome-cookies-with-python/
         - Windows: https://gist.github.com/jordan-wright/5770442#file-chrome_extract-py
         - Relevant Chromium source code: http://src.chromium.org/viewvc/chrome/trunk/src/components/os_crypt/
         """
        salt = b'saltysalt'
        iv = b' ' * 16
        length = 16

        def chrome_decrypt(encrypted, key=None):
            # Encrypted cookies should be prefixed with 'v10' according to the
            # Chromium code. Strip it off.
            encrypted = encrypted[3:]

            # Strip padding by taking off number indicated by padding
            # eg if last is '\x0e' then ord('\x0e') == 14, so take off 14.
            def clean(x):
                return x[:-ord(x[-1])]

            cipher = AES.new(key, AES.MODE_CBC, IV=iv)
            decrypted = cipher.decrypt(encrypted)

            return clean(decrypted)

        decrypted_value = "<error>"
        if encrypted_value is not None:
            if len(encrypted_value) >= 2:
                # If running Chrome on Windows
                if sys.platform == 'win32' and self.available_decrypts['windows'] == 1:
                    try:
                        decrypted_value = win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1]
                    except:
                        decrypted_value = "<encrypted>"
                # If running Chrome on OSX
                elif sys.platform == 'darwin' and self.available_decrypts['mac'] == 1:
                    try:
                        if not self.cached_key:
                            my_pass = keyring.get_password('Chrome Safe Storage', 'Chrome')
                            my_pass = my_pass.encode('utf8')
                            iterations = 1003
                            self.cached_key = PBKDF2(my_pass, salt, length, iterations)
                        decrypted_value = chrome_decrypt(encrypted_value, key=self.cached_key)
                    except:
                        pass
                else:
                    decrypted_value = "<encrypted>"

                # If running Chromium on Linux.
                # Unlike Win/Mac, we can decrypt Linux cookies without the user's pw
                if decrypted_value == "<encrypted>" and self.available_decrypts['linux'] == 1:
                    try:
                        if not self.cached_key:
                            my_pass = 'peanuts'
                            iterations = 1
                            self.cached_key = PBKDF2(my_pass, salt, length, iterations)
                        decrypted_value = chrome_decrypt(encrypted_value, key=self.cached_key)
                    except:
                        pass

        return decrypted_value

    def get_cookies(self, path, database, version):
        # Set up empty return array
        results = []

        log.info(f'Cookie items from {database}:')

        # Queries for different versions
        query = {66: '''SELECT cookies.host_key, cookies.path, cookies.name, cookies.value, cookies.creation_utc,
                            cookies.last_access_utc, cookies.expires_utc, cookies.is_secure AS secure, 
                            cookies.is_httponly AS httponly, cookies.is_persistent AS persistent, 
                            cookies.has_expires, cookies.priority, cookies.encrypted_value
                        FROM cookies''',
                 33: '''SELECT cookies.host_key, cookies.path, cookies.name, cookies.value, cookies.creation_utc,
                            cookies.last_access_utc, cookies.expires_utc, cookies.secure, cookies.httponly,
                            cookies.persistent, cookies.has_expires, cookies.priority, cookies.encrypted_value
                        FROM cookies''',
                 28: '''SELECT cookies.host_key, cookies.path, cookies.name, cookies.value, cookies.creation_utc,
                            cookies.last_access_utc, cookies.expires_utc, cookies.secure, cookies.httponly,
                            cookies.persistent, cookies.has_expires, cookies.priority
                        FROM cookies''',
                 17: '''SELECT cookies.host_key, cookies.path, cookies.name, cookies.value, cookies.creation_utc,
                            cookies.last_access_utc, cookies.expires_utc, cookies.secure, cookies.httponly,
                            cookies.persistent, cookies.has_expires
                        FROM cookies''',
                 1:  '''SELECT cookies.host_key, cookies.path, cookies.name, cookies.value, cookies.creation_utc,
                            cookies.last_access_utc, cookies.expires_utc, cookies.secure, cookies.httponly
                        FROM cookies'''}

        # Get the lowest possible version from the version list, and decrement it until it finds a matching query
        compatible_version = version[0]
        while compatible_version not in list(query.keys()) and compatible_version > 0:
            compatible_version -= 1

        if compatible_version != 0:
            log.info(f' - Using SQL query for Cookie items for Chrome v{compatible_version}')
            try:
                # Copy and connect to copy of 'Cookies' SQLite DB
                conn = utils.open_sqlite_db(self, path, database)
                if not conn:
                    self.artifacts_counts[database] = 'Failed'
                    return
                cursor = conn.cursor()

                # Use highest compatible version SQL to select download data
                cursor.execute(query[compatible_version])

                for row in cursor:
                    if row.get('encrypted_value') is not None:
                        if len(row.get('encrypted_value')) >= 2:
                            cookie_value = self.decrypt_cookie(row.get('encrypted_value'))
                        else:
                            cookie_value = row.get('value')
                    else:
                        cookie_value = row.get('value')

                    new_row = Chrome.CookieItem(
                        self.profile_path, row.get('host_key'), row.get('path'), row.get('name'), cookie_value,
                        utils.to_datetime(row.get('creation_utc'), self.timezone),
                        utils.to_datetime(row.get('last_access_utc'), self.timezone), row.get('secure'),
                        row.get('httponly'), row.get('persistent'), row.get('has_expires'),
                        utils.to_datetime(row.get('expires_utc'), self.timezone), row.get('priority'))

                    accessed_row = Chrome.CookieItem(
                        self.profile_path, row.get('host_key'), row.get('path'), row.get('name'), cookie_value,
                        utils.to_datetime(row.get('creation_utc'), self.timezone),
                        utils.to_datetime(row.get('last_access_utc'), self.timezone), row.get('secure'),
                        row.get('httponly'), row.get('persistent'), row.get('has_expires'),
                        utils.to_datetime(row.get('expires_utc'), self.timezone), row.get('priority'))

                    new_row.url = (new_row.host_key + new_row.path)
                    accessed_row.url = (accessed_row.host_key + accessed_row.path)

                    # Create the row for when the cookie was created
                    new_row.row_type = 'cookie (created)'
                    new_row.timestamp = new_row.creation_utc
                    results.append(new_row)

                    # If the cookie was created and accessed at the same time (only used once), or if the last accessed
                    # time is 0 (happens on iOS), don't create an accessed row
                    if new_row.creation_utc != new_row.last_access_utc and \
                            accessed_row.last_access_utc != utils.to_datetime(0, self.timezone):
                        accessed_row.row_type = 'cookie (accessed)'
                        accessed_row.timestamp = accessed_row.last_access_utc
                        results.append(accessed_row)

                conn.close()
                self.artifacts_counts[database] = len(results)
                log.info(f' - Parsed {len(results)} items')
                self.parsed_artifacts.extend(results)

            except Exception as e:
                self.artifacts_counts[database] = 'Failed'
                log.error(f' - Could not open {os.path.join(path, database)}')

    def get_login_data(self, path, database, version):
        # Set up empty return array
        results = []

        log.info(f'Login items from {database}:')

        # Queries for "logins" table for different versions
        query = {78:  '''SELECT origin_url, action_url, username_element, username_value, password_element,
                            password_value, date_created, date_last_used, blacklisted_by_user, 
                            times_used FROM logins''',
                 29:  '''SELECT origin_url, action_url, username_element, username_value, password_element,
                            password_value, date_created, blacklisted_by_user, times_used FROM logins''',
                 6:  '''SELECT origin_url, action_url, username_element, username_value, password_element,
                            password_value, date_created, blacklisted_by_user FROM logins'''}

        # Get the lowest possible version from the version list, and decrement it until it finds a matching query
        compatible_version = version[0]
        while compatible_version not in list(query.keys()) and compatible_version > 0:
            compatible_version -= 1

        if compatible_version != 0:
            log.info(f' - Using SQL query for Login items for Chrome v{compatible_version}')

            # Copy and connect to copy of 'Login Data' SQLite DB
            conn = utils.open_sqlite_db(self, path, database)
            if not conn:
                self.artifacts_counts[database] = 'Failed'
                return
            cursor = conn.cursor()

            # Use highest compatible version SQL to select download data
            cursor.execute(query[compatible_version])

            for row in cursor:
                if row.get('blacklisted_by_user') == 1:
                    never_save_row = Chrome.LoginItem(
                        self.profile_path, utils.to_datetime(row.get('date_created'), self.timezone),
                        url=row.get('origin_url'), name=row.get('username_element'),
                        value='', count=row.get('times_used'),
                        interpretation='User chose to "Never save password" for this site')
                    never_save_row.row_type = 'login (never save)'
                    results.append(never_save_row)

                elif row.get('username_value'):
                    username_row = Chrome.LoginItem(
                        self.profile_path, utils.to_datetime(row.get('date_created'), self.timezone),
                        url=row.get('action_url'), name=row.get('username_element'),
                        value=row.get('username_value'), count=row.get('times_used'),
                        interpretation=f'User chose to save the credentials entered '
                                       f'(times used: {row.get("times_used")})')
                    username_row.row_type = 'login (saved credentials)'
                    results.append(username_row)

                    # 'date_last_used' was added in v78; some older records may have small, invalid values; skip them.
                    if row.get('date_last_used') and int(row.get('date_last_used')) > 13100000000000000:
                        username_row = Chrome.LoginItem(
                            self.profile_path, utils.to_datetime(row.get('date_last_used'), self.timezone),
                            url=row.get('action_url'), name=row.get('username_element'),
                            value=row.get('username_value'), count=row.get('times_used'),
                            interpretation=f'User tried to log in with this username (may or may not '
                                           f'have succeeded; times used: {row.get("times_used")})')
                        username_row.row_type = 'login (username)'
                        results.append(username_row)

                if row.get('password_value') is not None and self.available_decrypts['windows'] == 1:
                    try:
                        # Windows is all I've had time to test; Ubuntu uses built-in password manager
                        password = win32crypt.CryptUnprotectData(
                            row.get('password_value').decode(), None, None, None, 0)[1]
                    except:
                        password = self.decrypt_cookie(row.get('password_value'))

                    password_row = Chrome.LoginItem(
                        self.profile_path, utils.to_datetime(row.get('date_created'), self.timezone),
                        url=row.get('action_url'), name=row.get('password_element'),
                        value=password, count=row.get('times_used'),
                        interpretation='User chose to save the credentials entered')
                    password_row.row_type = 'login (password)'
                    results.append(password_row)

            conn.close()

            # Queries for "stats" table for different versions
            query = {48: '''SELECT origin_domain, username_value, dismissal_count, update_time FROM stats'''}

            # Get the lowest possible version from the version list, and decrement it until it finds a matching query
            compatible_version = version[0]
            while compatible_version not in list(query.keys()) and compatible_version > 0:
                compatible_version -= 1

            if compatible_version != 0:
                log.info(f' - Using SQL query for Login Stat items for Chrome v{compatible_version}')

                # Copy and connect to copy of 'Login Data' SQLite DB
                conn = utils.open_sqlite_db(self, path, database)
                if not conn:
                    self.artifacts_counts[database] = 'Failed'
                    return
                cursor = conn.cursor()

                # Use highest compatible version SQL to select download data
                cursor.execute(query[compatible_version])

                for row in cursor:
                    stats_row = Chrome.LoginItem(
                        self.profile_path, utils.to_datetime(row.get('update_time'), self.timezone),
                        url=row.get('origin_domain'), name='',
                        value=row.get('username_value'), count=row.get('dismissal_count'),
                        interpretation=f'User declined to save the password for this site '
                                       f'(dismissal count: {row.get("dismissal_count")})')
                    stats_row.row_type = 'login (declined save)'
                    results.append(stats_row)
                conn.close()

        self.artifacts_counts['Login Data'] = len(results)
        log.info(f' - Parsed {len(results)} items')
        self.parsed_artifacts.extend(results)

    def get_autofill(self, path, database, version):
        # Set up empty return array
        results = []

        log.info(f'Autofill items from {database}:')

        # Queries for different versions
        query = {35: '''SELECT autofill.date_created, autofill.date_last_used, autofill.name, autofill.value,
                        autofill.count FROM autofill''',
                 2: '''SELECT autofill_dates.date_created, autofill.name, autofill.value, autofill.count
                        FROM autofill, autofill_dates WHERE autofill.pair_id = autofill_dates.pair_id'''}

        # Get the lowest possible version from the version list, and decrement it until it finds a matching query
        compatible_version = version[0]
        while compatible_version not in list(query.keys()) and compatible_version > 0:
            compatible_version -= 1

        if compatible_version != 0:
            log.info(f' - Using SQL query for Autofill items for Chrome v{compatible_version}')
            try:
                # Copy and connect to copy of 'Web Data' SQLite DB
                conn = utils.open_sqlite_db(self, path, database)
                if not conn:
                    self.artifacts_counts['Autofill'] = 'Failed'
                    return
                cursor = conn.cursor()

                # Use highest compatible version SQL to select download data
                cursor.execute(query[compatible_version])

                for row in cursor:
                    autofill_value = row.get('value')
                    if isinstance(autofill_value, bytes):
                        autofill_value = '<encrypted>'

                    results.append(Chrome.AutofillItem(
                        self.profile_path, utils.to_datetime(row.get('date_created'), self.timezone),
                        row.get('name'), autofill_value, row.get('count')))

                    if row.get('date_last_used') and row.get('count') > 1:
                        results.append(Chrome.AutofillItem(
                            self.profile_path, utils.to_datetime(row.get('date_last_used'), self.timezone),
                            row.get('name'), autofill_value, row.get('count')))

                conn.close()
                self.artifacts_counts['Autofill'] = len(results)
                log.info(f' - Parsed {len(results)} items')
                self.parsed_artifacts.extend(results)

            except Exception as e:
                self.artifacts_counts['Autofill'] = 'Failed'
                log.error(f' - Could not open {os.path.join(path, database)}: {e}')

    def get_bookmarks(self, path, file, version):
        # Set up empty return array
        results = []

        log.info(f'Bookmark items from {file}:')

        # Connect to 'Bookmarks' JSON file
        bookmarks_path = os.path.join(path, file)

        try:
            with open(bookmarks_path, encoding='utf-8', errors='replace') as f:
                decoded_json = json.loads(f.read())

            log.info(f' - Reading from file "{bookmarks_path}"')

            # TODO: sync_id
            def process_bookmark_children(parent, children):
                for child in children:
                    if child['type'] == 'url':
                        results.append(Chrome.BookmarkItem(
                            self.profile_path, utils.to_datetime(child['date_added'], self.timezone),
                            child['name'], child['url'], parent))
                        
                    elif child['type'] == 'folder':
                        new_parent = parent + ' > ' + child['name']
                        results.append(Chrome.BookmarkFolderItem(
                            self.profile_path, utils.to_datetime(child['date_added'], self.timezone),
                            child['date_modified'], child['name'], parent))
                        process_bookmark_children(new_parent, child['children'])

            for top_level_folder in list(decoded_json['roots'].keys()):
                if top_level_folder == 'synced':
                    if decoded_json['roots'][top_level_folder]['children'] is not None:
                        process_bookmark_children(f"Synced > {decoded_json['roots'][top_level_folder]['name']}",
                                                  decoded_json['roots'][top_level_folder]['children'])
                elif top_level_folder != 'sync_transaction_version' and top_level_folder != 'meta_info':
                    if decoded_json['roots'][top_level_folder]['children'] is not None:
                        process_bookmark_children(decoded_json['roots'][top_level_folder]['name'],
                                                  decoded_json['roots'][top_level_folder]['children'])

            self.artifacts_counts['Bookmarks'] = len(results)
            log.info(f' - Parsed {len(results)} items')
            self.parsed_artifacts.extend(results)

        except:
            log.error(f' - Error parsing "{bookmarks_path}"')
            self.artifacts_counts['Bookmarks'] = 'Failed'
            return

    def get_local_storage(self, path, dir_name):
        results = []

        # Grab file list of 'Local Storage' directory
        ls_path = os.path.join(path, dir_name)
        log.info('Local Storage:')
        log.info(f' - Reading from {ls_path}')

        local_storage_listing = os.listdir(ls_path)
        log.debug(f' - {len(local_storage_listing)} files in Local Storage directory')
        filtered_listing = []

        # Chrome v61+ used leveldb for LocalStorage, but kept old SQLite .localstorage files if upgraded.
        if 'leveldb' in local_storage_listing:
            log.debug(f' - Found "leveldb" directory; reading Local Storage LevelDB records')
            ls_ldb_path = os.path.join(ls_path, 'leveldb')
            ls_ldb_records = utils.get_ldb_records(ls_ldb_path)
            log.debug(f' - Reading {len(ls_ldb_records)} Local Storage raw LevelDB records; beginning parsing')
            for record in ls_ldb_records:
                ls_item = self.parse_ls_ldb_record(record)
                if ls_item and ls_item.get('record_type') == 'entry':
                    results.append(Chrome.LocalStorageItem(
                        self.profile_path, ls_item['origin'], ls_item['key'], ls_item['value'],
                        ls_item['seq'], ls_item['state'], str(ls_item['origin_file'])))

        # Chrome v60 and earlier used a SQLite file (with a .localstorage file ext) for each origin
        for ls_file in local_storage_listing:
            if ls_file.startswith(('ftp', 'http', 'file', 'chrome-extension')) and ls_file.endswith('.localstorage'):
                filtered_listing.append(ls_file)
                ls_file_path = os.path.join(ls_path, ls_file)
                ls_created = os.stat(ls_file_path).st_ctime

                try:
                    # Copy and connect to copy of the Local Storage SQLite DB
                    conn = utils.open_sqlite_db(self, ls_path, ls_file)
                    cursor = conn.cursor()

                    cursor.execute('SELECT key,value,rowid FROM ItemTable')
                    for row in cursor:
                        try:
                            printable_value = row.get('value', b'').decode('utf-16')
                        except:
                            printable_value = repr(row.get('value'))

                        results.append(Chrome.LocalStorageItem(
                            profile=self.profile_path, origin=ls_file[:-13], key=row.get('key', ''),
                            value=printable_value, seq=row.get('rowid', 0), state='Live',
                            last_modified=utils.to_datetime(ls_created, self.timezone),
                            source_path=os.path.join(ls_path, ls_file)))

                    conn.close()

                except Exception as e:
                    log.warning(f' - Error reading key/values from {ls_file_path}: {e}')
                    pass

        self.artifacts_counts['Local Storage'] = len(results)
        log.info(f' - Parsed {len(results)} items from {len(filtered_listing)} files')
        self.parsed_storage.extend(results)

    def get_session_storage(self, path, dir_name):
        results = []

        # Grab file list of 'Session Storage' directory
        ss_path = os.path.join(path, dir_name)
        log.info('Session Storage:')
        log.info(f' - Reading from {ss_path}')

        session_storage_listing = os.listdir(ss_path)
        log.debug(f' - {len(session_storage_listing)} files in Session Storage directory')

        # Session Storage parsing is thanks to Alex Caithness of CCL Forensics; ccl_chrome_indexeddb
        # is bundled with Hindsight with his consent (and our thanks!). The below logic is adapted
        # from his Chromium_dump_session_storage.py script.
        import pathlib
        from pyhindsight.lib.ccl_chrome_indexeddb import ccl_chromium_sessionstorage
        ss_ldb_records = None

        try:
            ss_ldb_records = ccl_chromium_sessionstorage.SessionStoreDb(pathlib.Path(ss_path))
        except ValueError as e:
            log.warning(f' - Error reading records; possible LevelDB corruption')
            self.artifacts_counts['Session Storage'] = 'Failed'

        if ss_ldb_records:
            for origin in ss_ldb_records.iter_hosts():
                origin_kvs = ss_ldb_records.get_all_for_host(origin)
                for key, values in origin_kvs.items():
                    for value in values:
                        results.append(Chrome.SessionStorageItem(
                            self.profile_path, origin, key, value.value,
                            value.leveldb_sequence_number, 'Live', ss_path))

            # Some records don't have an associated host for some unknown reason; still include them.
            for key, value in ss_ldb_records.iter_orphans():
                results.append(Chrome.SessionStorageItem(
                    self.profile_path, '<orphan>', key, value.value,
                    value.leveldb_sequence_number, 'Live', ss_path))

            ss_ldb_records.close()
            self.artifacts_counts['Session Storage'] = len(results)

        log.info(f' - Parsed {len(results)} Session Storage items')
        self.parsed_storage.extend(results)

    def get_extensions(self, path, dir_name):
        results = []
        log.info('Extensions:')

        # Profile folder
        try:
            profile = os.path.split(path)[1]
        except:
            profile = 'error'

        # Grab listing of 'Extensions' directory
        ext_path = os.path.join(path, dir_name)
        log.info(f' - Reading from {ext_path}')
        ext_listing = os.listdir(ext_path)
        log.debug(f' - {len(ext_listing)} files in Extensions directory: {str(ext_listing)}')

        # Only process directories with the expected naming convention
        app_id_re = re.compile(r'^([a-z]{32})$')
        ext_listing = [x for x in ext_listing if app_id_re.match(x)]
        log.debug(f' - {len(ext_listing)} files in Extensions directory will be processed: {str(ext_listing)}')

        # Process each directory with an app_id name
        for app_id in ext_listing:
            # Get listing of the contents of app_id directory; should contain subdirs for each version of the extension.
            ext_vers_listing = os.path.join(ext_path, app_id)
            ext_vers = os.listdir(ext_vers_listing)
            manifest_file = None
            selected_version = None

            # Connect to manifest.json in latest version directory
            for version in sorted(ext_vers, reverse=True, key=lambda x: int(x.split('.', maxsplit=1)[0])):
                manifest_path = os.path.join(ext_vers_listing, version, 'manifest.json')
                try:
                    with open(manifest_path, encoding='utf-8', errors='replace') as f:
                        decoded_manifest = json.loads(f.read())
                    selected_version = version
                    break
                except (IOError, json.JSONDecodeError) as e:
                    log.error(f' - Error opening {manifest_path} for extension {app_id}; {e}')
                    continue

            if not decoded_manifest:
                log.error(f' - Error opening manifest info for extension {app_id}')
                continue

            name = None
            description = None

            try:
                if decoded_manifest['name'].startswith('__'):
                    if decoded_manifest['default_locale']:
                        locale_messages_path = os.path.join(
                            ext_vers_listing, selected_version, '_locales', decoded_manifest['default_locale'],
                            'messages.json')
                        with open(locale_messages_path, encoding='utf-8', errors='replace') as f:
                            decoded_locale_messages = json.loads(f.read())

                        try:
                            name = decoded_locale_messages[decoded_manifest['name'][6:-2]]['message']
                        except KeyError:
                            try:
                                name = decoded_locale_messages[decoded_manifest['name'][6:-2]].lower['message']
                            except KeyError:
                                try:
                                    # Google Wallet / Chrome Payments is weird/hidden - name is saved different
                                    # than other extensions
                                    name = decoded_locale_messages['app_name']['message']
                                except:
                                    log.warning(f' - Error reading \'name\' for {app_id}')
                                    name = '<error>'
                else:
                    try:
                        name = decoded_manifest['name']
                    except KeyError:
                        name = None
                        log.error(f' - Error reading \'name\' for {app_id}')

                if 'description' in list(decoded_manifest.keys()):
                    if decoded_manifest['description'].startswith('__'):
                        if decoded_manifest['default_locale']:
                            locale_messages_path = os.path.join(
                                ext_vers_listing, selected_version, '_locales', decoded_manifest['default_locale'],
                                'messages.json')
                            with open(locale_messages_path, encoding='utf-8', errors='replace') as f:
                                decoded_locale_messages = json.loads(f.read())

                            try:
                                description = decoded_locale_messages[decoded_manifest['description'][6:-2]]['message']
                            except KeyError:
                                try:
                                    description = decoded_locale_messages[
                                        decoded_manifest['description'][6:-2]].lower['message']
                                except KeyError:
                                    try:
                                        # Google Wallet / Chrome Payments is weird/hidden - name is saved different
                                        # than other extensions
                                        description = decoded_locale_messages['app_description']['message']
                                    except:
                                        description = '<error>'
                                        log.error(f' - Error reading \'message\' for {app_id}')
                    else:
                        try:
                            description = decoded_manifest['description']
                        except KeyError:
                            description = None
                            log.warning(f' - Error reading \'description\' for {app_id}')

                results.append(Chrome.BrowserExtension(profile, app_id, name, description, decoded_manifest['version']))
            except:
                log.error(f' - Error decoding manifest file for {app_id}')
                pass

        self.artifacts_counts['Extensions'] = len(results)
        log.info(f' - Parsed {len(results)} items')
        presentation = {'title': 'Installed Extensions',
                        'columns': [
                            {'display_name': 'Extension Name',
                             'data_name': 'name',
                             'display_width': 26},
                            {'display_name': 'Description',
                             'data_name': 'description',
                             'display_width': 60},
                            {'display_name': 'Version',
                             'data_name': 'version',
                             'display_width': 10},
                            {'display_name': 'App ID',
                             'data_name': 'app_id',
                             'display_width': 36},
                            {'display_name': 'Profile Folder',
                             'data_name': 'profile',
                             'display_width': 30}
                        ]}
        self.installed_extensions = {'data': results, 'presentation': presentation}

    def get_preferences(self, path, preferences_file):
        def check_and_append_pref(parent, pref, value=None, description=None):
            try:
                # If the preference exists, continue
                if pref in parent.keys():
                    # If no value is specified, use the value from the preference JSON
                    if not value:
                        value = parent[pref]
                    # Append the preference dict to our results array
                    results.append({
                        'group': None,
                        'name': pref,
                        'value': value,
                        'description': description
                    })

                else:
                    results.append({
                        'group': None,
                        'name': pref,
                        'value': '<not present>',
                        'description': description
                    })

            except Exception as e:
                log.exception(f' - Exception parsing Preference item: {e}')

        def check_and_append_pref_and_children(parent, pref, value=None, description=None):
            # If the preference exists, continue
            if parent.get(pref):
                # If no value is specified, use the value from the preference JSON
                if not value:
                    value = parent[pref]
                # Append the preference dict to our results array
                results.append({
                    'group': None,
                    'name': pref,
                    'value': value,
                    'description': description
                })

            else:
                results.append({
                    'group': None,
                    'name': pref,
                    'value': '<not present>',
                    'description': description
                })

        def append_group(group, description=None):
            # Append the preference group to our results array
            results.append({
                'group': group,
                'name': None,
                'value': None,
                'description': description
            })

        def append_pref(pref, value=None, description=None):
            results.append({
                'group': None,
                'name': pref,
                'value': value,
                'description': description
            })

        def expand_language_code(code):
            # From https://cs.chromium.org/chromium/src/components/translate/core/browser/translate_language_list.cc
            codes = {
                  'af': 'Afrikaans',
                  'am': 'Amharic',
                  'ar': 'Arabic',
                  'az': 'Azerbaijani',
                  'be': 'Belarusian',
                  'bg': 'Bulgarian',
                  'bn': 'Bengali',
                  'bs': 'Bosnian',
                  'ca': 'Catalan',
                  'ceb': 'Cebuano',
                  'co': 'Corsican',
                  'cs': 'Czech',
                  'cy': 'Welsh',
                  'da': 'Danish',
                  'de': 'German',
                  'el': 'Greek',
                  'en': 'English',
                  'eo': 'Esperanto',
                  'es': 'Spanish',
                  'et': 'Estonian',
                  'eu': 'Basque',
                  'fa': 'Persian',
                  'fi': 'Finnish',
                  'fy': 'Frisian',
                  'fr': 'French',
                  'ga': 'Irish',
                  'gd': 'Scots Gaelic',
                  'gl': 'Galician',
                  'gu': 'Gujarati',
                  'ha': 'Hausa',
                  'haw': 'Hawaiian',
                  'hi': 'Hindi',
                  'hr': 'Croatian',
                  'ht': 'Haitian Creole',
                  'hu': 'Hungarian',
                  'hy': 'Armenian',
                  'id': 'Indonesian',
                  'ig': 'Igbo',
                  'is': 'Icelandic',
                  'it': 'Italian',
                  'iw': 'Hebrew',
                  'ja': 'Japanese',
                  'ka': 'Georgian',
                  'kk': 'Kazakh',
                  'km': 'Khmer',
                  'kn': 'Kannada',
                  'ko': 'Korean',
                  'ku': 'Kurdish',
                  'ky': 'Kyrgyz',
                  'la': 'Latin',
                  'lb': 'Luxembourgish',
                  'lo': 'Lao',
                  'lt': 'Lithuanian',
                  'lv': 'Latvian',
                  'mg': 'Malagasy',
                  'mi': 'Maori',
                  'mk': 'Macedonian',
                  'ml': 'Malayalam',
                  'mn': 'Mongolian',
                  'mr': 'Marathi',
                  'ms': 'Malay',
                  'mt': 'Maltese',
                  'my': 'Burmese',
                  'ne': 'Nepali',
                  'nl': 'Dutch',
                  'no': 'Norwegian',
                  'ny': 'Nyanja',
                  'pa': 'Punjabi',
                  'pl': 'Polish',
                  'ps': 'Pashto',
                  'pt': 'Portuguese',
                  'ro': 'Romanian',
                  'ru': 'Russian',
                  'sd': 'Sindhi',
                  'si': 'Sinhala',
                  'sk': 'Slovak',
                  'sl': 'Slovenian',
                  'sm': 'Samoan',
                  'sn': 'Shona',
                  'so': 'Somali',
                  'sq': 'Albanian',
                  'sr': 'Serbian',
                  'st': 'Southern Sotho',
                  'su': 'Sundanese',
                  'sv': 'Swedish',
                  'sw': 'Swahili',
                  'ta': 'Tamil',
                  'te': 'Telugu',
                  'tg': 'Tajik',
                  'th': 'Thai',
                  'tl': 'Tagalog',
                  'tr': 'Turkish',
                  'uk': 'Ukrainian',
                  'ur': 'Urdu',
                  'uz': 'Uzbek',
                  'vi': 'Vietnamese',
                  'yi': 'Yiddish',
                  'xh': 'Xhosa',
                  'yo': 'Yoruba',
                  'zh-CN': 'Chinese (Simplified)',
                  'zh-TW': 'Chinese (Traditional)',
                  'zu': 'Zulu'
                }
            return codes.get(code, code)

        results = []
        timestamped_preference_items = []
        log.info('Preferences:')

        # Open 'Preferences' file
        pref_path = os.path.join(path, preferences_file)
        try:
            log.info(f' - Reading from {pref_path}')
            with open(pref_path, encoding='utf-8', errors='replace') as f:
                prefs = json.loads(f.read())

        except Exception as e:
            log.exception(f' - Error decoding Preferences file {pref_path}: {e}')
            self.artifacts_counts[preferences_file] = 'Failed'
            return

        # Account Information
        if prefs.get('account_info'):
            append_group('Account Information')
            for account in prefs['account_info']:
                for account_item in list(account.keys()):
                    if account_item == 'accountcapabilities':
                        continue
                    append_pref(account_item, account[account_item])

        # Local file paths
        append_group('Local file paths')
        if prefs.get('download'):
            check_and_append_pref(prefs['download'], 'default_directory')
        if prefs.get('printing'):
            if prefs.get('print_preview_sticky_settings'):
                check_and_append_pref(prefs['printing']['print_preview_sticky_settings'], 'savePath')
        if prefs.get('savefile'):
            check_and_append_pref(prefs['savefile'], 'default_directory')
        if prefs.get('selectfile'):
            check_and_append_pref(prefs['selectfile'], 'last_directory')

        # Autofill
        if prefs.get('autofill'):
            append_group('Autofill')
            check_and_append_pref(prefs['autofill'], 'enabled')

        # Network Prediction
        if prefs.get('net'):
            # Ref: https://source.chromium.org/chromium/chromium/src/+/main:chrome/browser/net/prediction_options.h
            NETWORK_PREDICTION_OPTIONS = {
                0: 'Always',
                1: 'WIFI Only',
                2: 'Never'
            }
            append_group('Network Prefetching')
            check_and_append_pref(prefs['net'], 'network_prediction_options',
                                  NETWORK_PREDICTION_OPTIONS.get(prefs['net'].get('network_prediction_options')))

        # Clearing Chrome Data
        if prefs.get('browser'):
            append_group('Clearing Chrome Data')
            if prefs['browser'].get('last_clear_browsing_data_time'):
                check_and_append_pref(
                    prefs['browser'], 'last_clear_browsing_data_time',
                    utils.friendly_date(prefs['browser']['last_clear_browsing_data_time']),
                    'Last time the history was cleared')
            check_and_append_pref(prefs['browser'], 'clear_lso_data_enabled')
            if prefs['browser'].get('clear_data'):
                try:
                    check_and_append_pref(
                        prefs['browser']['clear_data'], 'time_period',
                        description='0: past hour; 1: past day; 2: past week; 3: last 4 weeks; '
                                    '4: the beginning of time')
                    check_and_append_pref(prefs['browser']['clear_data'], 'content_licenses')
                    check_and_append_pref(prefs['browser']['clear_data'], 'hosted_apps_data')
                    check_and_append_pref(prefs['browser']['clear_data'], 'cookies')
                    check_and_append_pref(prefs['browser']['clear_data'], 'download_history')
                    check_and_append_pref(prefs['browser']['clear_data'], 'browsing_history')
                    check_and_append_pref(prefs['browser']['clear_data'], 'passwords')
                    check_and_append_pref(prefs['browser']['clear_data'], 'form_data')
                except Exception as e:
                    log.exception(f' - Exception parsing Preference item: {e})')

        append_group('Per Host Zoom Levels', 'These settings persist even when the history is cleared, and may be '
                                             'useful in some cases.')

        # Source: https://source.chromium.org/chromium/chromium/src/+/main:third_party/blink/common/page/page_zoom.cc
        def zoom_level_to_zoom_factor(zoom_level):
            if not zoom_level:
                return ''
            try:
                zoom_factor = round(math.pow(1.2, zoom_level), 2)
                return f'{zoom_factor:.0%}'
            except:
                return zoom_level

        # There may be per_host_zoom_levels keys in at least two locations: profile.per_host_zoom_levels and
        # partition.per_host_zoom_levels. The "profile." location may have been deprecated; unsure.
        if prefs.get('profile'):
            if prefs['profile'].get('per_host_zoom_levels'):
                try:
                    for zoom in list(prefs['profile']['per_host_zoom_levels'].keys()):
                        check_and_append_pref(prefs['profile']['per_host_zoom_levels'], zoom,
                                              zoom_level_to_zoom_factor(zoom))
                except Exception as e:
                    log.exception(f' - Exception parsing Preference item: {e})')

        if prefs.get('partition'):
            if prefs['partition'].get('per_host_zoom_levels'):
                try:
                    for partition_key, zoom_levels in list(prefs['partition']['per_host_zoom_levels'].items()):
                        for host, config in zoom_levels.items():
                            if isinstance(config, float):
                                # Example:
                                #  "dfir.blog": -0.5778829311823857
                                append_pref(host, zoom_level_to_zoom_factor(config))
                            elif isinstance(config, dict):
                                # Example:
                                # "dfir.blog": {
                                #     "last_modified": "13252995901366133",
                                #     "zoom_level": -0.5778829311823857
                                #   }
                                append_pref(host, zoom_level_to_zoom_factor(config.get('zoom_level')))
                                timestamped_preference_item = Chrome.SiteSetting(
                                    self.profile_path, url=host,
                                    timestamp=utils.to_datetime(config.get('last_modified'), self.timezone),
                                    key=f'per_host_zoom_levels [in {preferences_file}.partition]',
                                    value=f'Changed zoom level to {zoom_level_to_zoom_factor(config.get("zoom_level"))}',
                                    interpretation='')
                                timestamped_preference_item.row_type += ' (zoom level)'
                                timestamped_preference_items.append(timestamped_preference_item)
                except Exception as e:
                    log.exception(f' - Exception parsing Preference item: {e})')

        if prefs.get('password_manager'):
            if prefs['password_manager'].get('profile_store_date_last_used_for_filling'):
                timestamped_preference_item = Chrome.SiteSetting(
                    self.profile_path, url=None,
                    timestamp=utils.to_datetime(
                        prefs['password_manager']['profile_store_date_last_used_for_filling'], self.timezone),
                    key=f'profile_store_date_last_used_for_filling [in {preferences_file}.password_manager]',
                    value=prefs['password_manager']['profile_store_date_last_used_for_filling'], interpretation='')
                timestamped_preference_item.row_type += ' (password fill)'
                timestamped_preference_items.append(timestamped_preference_item)

        if prefs.get('profile'):
            if prefs['profile'].get('content_settings'):
                if prefs['profile']['content_settings'].get('pattern_pairs'):
                    try:
                        append_group('Profile Content Settings', 'These settings persist even when the history is '
                                                                 'cleared, and may be useful in some cases.')
                        for pair in list(prefs['profile']['content_settings']['pattern_pairs'].keys()):
                            # Adding the space before the domain prevents Excel from freaking out...  idk.
                            append_pref(' '+str(pair), str(prefs['profile']['content_settings']['pattern_pairs'][pair]))
                    except Exception as e:
                        log.exception(f' - Exception parsing Preference item: {e})')

                if prefs['profile']['content_settings'].get('exceptions'):

                    for exception_type, exception_data in prefs['profile']['content_settings']['exceptions'].items():
                        try:
                            for origin, pref_data in exception_data.items():
                                if pref_data.get('last_modified') and pref_data.get('last_modified') != '0':
                                    row_type_suffix = ' (modified)'
                                    interpretation = ''

                                    # The setting value can be an int that maps to an enum, or a dict for a more
                                    # complicated setting. If it's the simpler int value, translate the enum.
                                    content_settings_values = {
                                        0: 'default',
                                        1: 'allow',
                                        2: 'block'
                                    }

                                    if isinstance(pref_data.get('setting'), int):
                                        interpretation = f'"{exception_type}" set to {pref_data["setting"]} ' \
                                                         f'({content_settings_values.get(pref_data["setting"])})'

                                    pref_item = Chrome.SiteSetting(
                                        self.profile_path, url=origin,
                                        timestamp=utils.to_datetime(pref_data['last_modified'], self.timezone),
                                        key=f'{exception_type} '
                                            f'[in {preferences_file}.profile.content_settings.exceptions]',
                                        value=str(pref_data), interpretation=interpretation)
                                    pref_item.row_type += row_type_suffix
                                    timestamped_preference_items.append(pref_item)

                                if exception_type.endswith('_engagement'):
                                    row_type_suffix = ' (engagement)'
                                    media_playback_time = pref_data['setting'].get('lastMediaPlaybackTime', 0.0)
                                    engagement_time = pref_data['setting'].get('lastEngagementTime', 0.0)

                                    if media_playback_time:
                                        engagement_item = Chrome.SiteSetting(
                                            self.profile_path, url=origin,
                                            timestamp=utils.to_datetime(media_playback_time, self.timezone),
                                            key=f'lastMediaPlaybackTime in {preferences_file}.profile.'
                                                f'content_settings.exceptions.media_engagement]',
                                            value=str(pref_data), interpretation='')
                                        engagement_item.row_type += row_type_suffix
                                        timestamped_preference_items.append(engagement_item)

                                    elif engagement_time:
                                        engagement_item = Chrome.SiteSetting(
                                            self.profile_path, url=origin,
                                            timestamp=utils.to_datetime(engagement_time, self.timezone),
                                            key=f'lastEngagementTime in {preferences_file}.profile.'
                                                f'content_settings.exceptions.site_engagement]',
                                            value=str(pref_data), interpretation='')
                                        engagement_item.row_type += row_type_suffix
                                        timestamped_preference_items.append(engagement_item)

                        except Exception as e:
                            log.exception(f' - Exception parsing Preference item: {e})')

        if prefs.get('extensions'):
            if prefs['extensions'].get('autoupdate'):
                # Example (from in Preferences file):
                # "extensions": {
                #     ...
                #     "autoupdate": {
                #         "last_check": "13162668769688981",
                #         "next_check": "13162686093672995"
                #     },
                try:
                    if prefs['extensions']['autoupdate'].get('last_check'):
                        pref_item = Chrome.PreferenceItem(
                            self.profile_path, url='',
                            timestamp=utils.to_datetime(prefs['extensions']['autoupdate']['last_check'], self.timezone),
                            key=f'autoupdate.last_check [in {preferences_file}.extensions]',
                            value=prefs['extensions']['autoupdate']['last_check'], interpretation='')
                        timestamped_preference_items.append(pref_item)
                except Exception as e:
                    log.exception(f' - Exception parsing Preference item: {e})')

        if prefs.get('sessions'):
            if prefs['sessions'].get('event_log'):
                # Source: https://source.chromium.org/chromium/chromium/src/
                #  +/main:chrome/browser/sessions/session_service_log.h
                session_types = {
                    0: 'Start (The profile was started)',
                    1: 'Restore (A restore was triggered)',
                    2: 'Exit (The profile was shut down)',
                    3: 'Write Error (an error in writing the file occurred)'
                }

                for session_event in prefs['sessions']['event_log']:
                    pref_item = Chrome.PreferenceItem(
                        self.profile_path, url='',
                        timestamp=utils.to_datetime(session_event['time'], self.timezone),
                        key=f'Session event log [in {preferences_file}.sessions]',
                        value=str(session_event),
                        interpretation=f'{session_event["type"]} - '
                                       f'{session_types.get(session_event["type"], "Unknown type")}')
                    pref_item.row_type += ' (session)'
                    timestamped_preference_items.append(pref_item)

        if prefs.get('signin'):
            if prefs['signin'].get('signedin_time'):
                # Example (from in Preferences file):
                # "signin": {
                #     "signedin_time": "13196354823425155"
                #  },
                try:
                    pref_item = Chrome.PreferenceItem(
                        self.profile_path, url='',
                        timestamp=utils.to_datetime(prefs['signin']['signedin_time'], self.timezone),
                        key=f'signedin_time [in {preferences_file}.signin]',
                        value=prefs['signin']['signedin_time'], interpretation='')
                    timestamped_preference_items.append(pref_item)
                except Exception as e:
                    log.exception(f' - Exception parsing Preference item: {e})')

        if prefs.get('sync'):
            append_group('Sync Settings')
            if prefs['sync'].get('last_poll_time'):
                check_and_append_pref(prefs['sync'], 'last_poll_time',
                                      utils.friendly_date(prefs['sync']['last_poll_time']))

            if prefs['sync'].get('last_synced_time'):
                check_and_append_pref(prefs['sync'], 'last_synced_time',
                                      utils.friendly_date(prefs['sync']['last_synced_time']))

            sync_enabled_items = ['apps', 'autofill', 'bookmarks', 'cache_guid', 'extensions', 'gaia_id',
                                  'has_setup_completed', 'keep_everything_synced', 'passwords', 'preferences',
                                  'requested', 'tabs', 'themes', 'typed_urls']

            for sync_pref in list(prefs['sync'].keys()):
                if sync_pref not in sync_enabled_items:
                    continue

                check_and_append_pref(prefs['sync'], sync_pref)

        if prefs.get('translate_last_denied_time_for_language'):
            try:
                for lang_code, timestamp in prefs['translate_last_denied_time_for_language'].items():
                    # Example (from in Preferences file):
                    # "translate_last_denied_time_for_language": {
                    #   u'ar': 1438733440742.06,
                    #   u'th': [1447786189498.162],
                    #   u'hi': 1438798234384.275,
                    #  },
                    if isinstance(timestamp, list):
                        timestamp = timestamp[0]
                    assert isinstance(timestamp, float)
                    pref_item = Chrome.PreferenceItem(
                        self.profile_path, url='', timestamp=utils.to_datetime(timestamp, self.timezone),
                        key=f'translate_last_denied_time_for_language [in {preferences_file}]',
                        value=f'{lang_code}: {timestamp}',
                        interpretation=f'Declined to translate page from {expand_language_code(lang_code)}')
                    timestamped_preference_items.append(pref_item)
            except Exception as e:
                log.exception(f' - Exception parsing Preference item: {e})')

        self.parsed_artifacts.extend(timestamped_preference_items)

        self.artifacts_counts[preferences_file] = len(results) + len(timestamped_preference_items)
        log.info(f' - Parsed {len(results)} items')

        try:
            profile_folder = os.path.split(path)[1]
        except:
            profile_folder = 'error'

        presentation = {'title': f'Preferences ({profile_folder})',
                        'columns': [
                            {'display_name': 'Group',
                             'data_name': 'group',
                             'display_width': 8},
                            {'display_name': 'Setting Name',
                             'data_name': 'name',
                             'display_width': 40},
                            {'display_name': 'Value',
                             'data_name': 'value',
                             'display_width': 35},
                            {'display_name': 'Description',
                             'data_name': 'description',
                             'display_width': 60},
                            ]}

        self.preferences.append({'data': results, 'presentation': presentation})

    def get_cache(self, path, dir_name, row_type=None):
        """
        read the index file to walk whole cache // from cacheParse.py

        Reads the whole cache and store the collected data in a table
        or find out if the given list of urls is in the cache. If yes it
        return a list of the corresponding entries.
        """

        # Set up empty return array
        results = []

        path = os.path.join(path, dir_name)
        index_path = os.path.join(path, 'index')
        log.info(f'Cache items from {path}:')

        try:
            cacheBlock = CacheBlock(index_path)
            log.debug(" - Found cache index file: " + index_path)

            # Checking type
            if cacheBlock.type != CacheBlock.INDEX:
                log.error(' - \'index\' block file is invalid (has wrong magic type)')
                self.artifacts_counts[dir_name] = 'Failed'
                return
            log.debug(f' - Parsed index block file (version {cacheBlock.version})')
        except IOError as io_error:
            if io_error.errno == errno.ENOENT:
                log.error(" - No file called 'index' exists in the cache directory, {}".format(path))
            else:
                log.error(" - Failed to read index block file, {}".format(index_path))
            return
        except:
            log.error(' - Failed to parse index block file')
            return

        if cacheBlock.version != 2:
            log.error(' - Parsing CacheBlocks other than v2 is not supported')
            return

        try:
            index = open(os.path.join(path, 'index'), 'rb')
        except:
            log.error(f' - Error reading cache index file {os.path.join(path, "index")}')
            index.close()
            self.artifacts_counts[dir_name] = 'Failed'
            return

        # Skipping Header
        index.seek(92 * 4)

        for key in range(cacheBlock.tableSize):
            raw = struct.unpack('I', index.read(4))[0]
            if raw != 0:
                try:
                    entry = CacheEntry(self.profile_path, CacheAddress(raw, path=path), row_type, self.timezone)
                    # Add the new row to the results array
                    results.append(entry)
                except Exception as e:
                    log.error(f' - Error parsing cache entry {raw}: {str(e)}')

                try:
                    # Checking if there is a next item in the bucket because
                    # such entries are not stored in the Index File so they will
                    # be ignored during iterative lookup in the hash table
                    while entry.next != 0:
                        entry = CacheEntry(self.profile_path, CacheAddress(entry.next, path=path),
                                           row_type, self.timezone)
                        results.append(entry)
                except Exception as e:
                    log.error(f' - Error parsing cache entry {raw}: {str(e)}')

        index.close()

        cache_display_name = dir_name
        if dir_name == 'Cache_Data':
            cache_display_name = 'Cache'
        self.artifacts_counts[cache_display_name] = len(results)
        log.info(f' - Parsed {len(results)} items')
        self.parsed_artifacts.extend(results)

    def get_application_cache(self, path, dir_name, row_type=None):
        """
        read the index file to walk whole cache // from cacheParse.py

        Reads the whole cache and store the collected data in a table
        or find out if the given list of urls is in the cache. If yes it
        return a list of the corresponding entries.
        """

        # Set up empty return array
        results = []

        base_path = os.path.join(path, dir_name)
        cache_path = os.path.join(base_path, 'Cache')
        log.info(f'Application Cache items from {path}:')

        # Copy and connect to copy of 'Index' SQLite DB
        conn = utils.open_sqlite_db(self, base_path, 'Index')
        if not conn:
            self.artifacts_counts[dir_name] = 'Failed'
            return
        cursor = conn.cursor()

        try:
            cache_block = CacheBlock(os.path.join(cache_path, 'index'))
            # Checking type
            if cache_block.type != CacheBlock.INDEX:
                raise Exception('Invalid Index File')

            index = open(os.path.join(cache_path, 'index'), 'rb')
        except:
            log.error(f' - Error reading cache index file {os.path.join(path, "index")}')
            self.artifacts_counts[dir_name] = 'Failed'
            return

        # Skipping Header
        index.seek(92 * 4)

        for key in range(cache_block.tableSize):
            raw = struct.unpack('I', index.read(4))[0]
            if raw != 0:
                try:
                    entry = CacheEntry(self.profile_path, CacheAddress(raw, path=cache_path), row_type, self.timezone)
                    cursor.execute('''SELECT url from Entries WHERE response_id=?''', [entry.key])
                    index_url = cursor.fetchone()
                    if index_url:
                        entry.url = index_url['url']

                    # Add the new row to the results array
                    results.append(entry)

                    # Checking if there is a next item in the bucket because
                    # such entries are not stored in the Index File so they will
                    # be ignored during iterative lookup in the hash table
                    while entry.next != 0:
                        entry = CacheEntry(self.profile_path, CacheAddress(entry.next, path=cache_path), 
                                           row_type, self.timezone)
                        cursor.execute('''SELECT url FROM Entries WHERE response_id=?''', [entry.key])
                        index_url = cursor.fetchone()
                        if index_url:
                            entry.url = index_url['url']
                        results.append(entry)
                except Exception as e:
                    log.error(f' - Error parsing cache entry {raw}: {str(e)}')

        index.close()
        conn.close()

        self.artifacts_counts[dir_name] = len(results)
        log.info(f' - Parsed {len(results)} items')
        self.parsed_artifacts.extend(results)

    @staticmethod
    def parse_ls_ldb_record(record):
        """
        From https://cs.chromium.org/chromium/src/components/services/storage/dom_storage/local_storage_impl.cc:

        // LevelDB database schema
        // =======================
        //
        // Version 1 (in sorted order):
        //   key: "VERSION"
        //   value: "1"
        //
        //   key: "META:" + <url::Origin 'origin'>
        //   value: <LocalStorageOriginMetaData serialized as a string>
        //
        //   key: "_" + <url::Origin> 'origin'> + '\x00' + <script controlled key>
        //   value: <script controlled value>
        """
        parsed = {
            'seq': record['seq'],
            'state': record['state'],
            'origin_file': record['origin_file']
        }

        if record['key'].startswith('META:'.encode('utf-8')):
            parsed['record_type'] = 'META'
            parsed['origin'] = record['key'][5:].decode()
            parsed['key'] = record['key'][5:].decode()

            # From https://cs.chromium.org/chromium/src/components/services/storage/dom_storage/
            #   local_storage_database.proto:
            # message LocalStorageOriginMetaData
            #   required int64 last_modified = 1;
            #   required uint64 size_bytes = 2;
            # TODO: consider redoing this using protobufs
            if record['value'].startswith(b'\x08'):
                ptr = 1
                last_modified, bytes_read = utils.read_varint(record['value'][ptr:])
                size_bytes, _ = utils.read_varint(record['value'][ptr + bytes_read:])
                parsed['value'] = f'Last modified: {last_modified}; size: {size_bytes}'
            return parsed

        elif record['key'] == b'VERSION':
            return

        elif record['key'].startswith(b'_'):
            parsed['record_type'] = 'entry'
            try:
                parsed['origin'], parsed['key'] = record['key'][1:].split(b'\x00', 1)
                parsed['origin'] = parsed['origin'].decode()

                if parsed['key'].startswith(b'\x01'):
                    parsed['key'] = parsed['key'].lstrip(b'\x01').decode()

                elif parsed['key'].startswith(b'\x00'):
                    parsed['key'] = parsed['key'].lstrip(b'\x00').decode('utf-16')

            except Exception as e:
                log.error("Origin/key parsing error: {}".format(e))
                return

            try:
                if record['value'].startswith(b'\x01'):
                    parsed['value'] = record['value'].lstrip(b'\x01').decode('utf-8', errors='replace')

                elif record['value'].startswith(b'\x00'):
                    parsed['value'] = record['value'].lstrip(b'\x00').decode('utf-16', errors='replace')

                elif record['value'].startswith(b'\x08'):
                    parsed['value'] = record['value'].lstrip(b'\x08').decode()

                elif record['value'] == b'':
                    parsed['value'] = ''

            except Exception as e:
                log.error(f'Value parsing error: {e}')
                return

        for item in parsed.values():
            assert not isinstance(item, bytes)

        return parsed

    def build_logical_fs_path(self, node, parent_path=None):
        if not parent_path:
            parent_path = []

        parent_path.append(node['name'])
        node['path'] = parent_path
        for child_node in node['children'].values():
            self.build_logical_fs_path(child_node, parent_path=list(node['path']))

    def flatten_nodes_to_list(self, output_list, node):
        output_row = {
            'type': node['type'],
            'origin': node['path'][0],
            'logical_path': '\\'.join(node['path'][1:]),
            'local_path': node['fs_path'],
            'seq': node['seq'],
            'state': node['state'],
            'source_path': node['source_path'],
            'file_exists': node.get('file_exists'),
            'file_size': node.get('file_size'),
            'magic_results': node.get('magic_results')
        }

        if node.get('modification_time'):
            output_row['modification_time'] = utils.to_datetime(node['modification_time'])

        output_list.append(output_row)
        for child_node in node['children'].values():
            self.flatten_nodes_to_list(output_list, child_node)

    @staticmethod
    def get_local_file_info(file_path):
        file_size, magic_results = None, None
        exists = os.path.isfile(file_path)

        if exists:
            file_size = os.stat(file_path).st_size

        if file_size:
            magic_candidates = puremagic.magic_file(file_path)
            if magic_candidates:
                for magic_candidate in magic_candidates:
                    if magic_candidate.mime_type != '':
                        magic_results = f'{magic_candidate.mime_type} ({magic_candidate.confidence:.0%})'
                        break
                    else:
                        magic_results = f'{magic_candidate.name} ({magic_candidate.confidence:.0%})'

        return exists, file_size, magic_results

    def get_file_system(self, path, dir_name):

        result_list = []
        result_count = 0

        # Grab listing of 'File System' directory
        log.info('File System:')
        fs_root_path = os.path.join(path, dir_name)
        log.info(f' - Reading from {fs_root_path}')
        fs_root_listing = os.listdir(fs_root_path)
        log.debug(f' - {len(fs_root_listing)} files in File System directory: {str(fs_root_listing)}')

        # 'Origins' is a LevelDB that holds the mapping for each of the [000, 001, 002, ... ] dirs to
        # web origin (https_www.google.com_0)
        if 'Origins' in fs_root_listing:
            ldb_path = os.path.join(fs_root_path, 'Origins')
            origins = utils.get_ldb_records(ldb_path, 'ORIGIN:')
            for origin in origins:
                origin_domain = origin['key'].decode()
                origin_id = origin['value'].decode()
                origin_root_path = os.path.join(fs_root_path, origin_id)

                node_tree = {}
                backing_files = {}
                path_nodes = {
                    '0': {
                        'name': origin_domain, 'origin_id': origin_id, 'type': 'origin',
                        'fs_path': os.path.join('File System', origin_id),
                        'seq': origin['seq'], 'state': origin['state'],
                        'source_path': origin['origin_file'], 'children': {}
                    }
                }

                # Each Origin can have a temporary (t) and persistent (p) storage section.
                for fs_type in ['t', 'p']:
                    fs_type_path = os.path.join(origin_root_path, fs_type)
                    if not os.path.isdir(fs_type_path):
                        continue

                    log.debug(f' - Found \'{fs_type}\' data directory for origin {origin_domain}')

                    # Within each storage section is a 'Paths' leveldb, which holds the logical structure
                    # relationship between the files stored in this section.
                    fs_paths_path = os.path.join(fs_type_path, 'Paths')
                    if not os.path.isdir(fs_paths_path):
                        continue

                    # The 'Paths' ldbs can have entries of four different types:
                    # // - ("CHILD_OF:|parent_id|:<name>", "|file_id|"),
                    # // - ("LAST_FILE_ID", "|last_file_id|"),
                    # // - ("LAST_INTEGER", "|last_integer|"),
                    # // - ("|file_id|", "pickled FileInfo")
                    # // where FileInfo has |parent_id|, |data_path|, |name| and |modification_time|
                    # from cs.chromium.org/chromium/src/storage/browser/file_system/sandbox_directory_database.cc

                    path_items = utils.get_ldb_records(fs_paths_path)

                    # Loop over records looking for "file_id" records to build backing_files dict. We skip
                    # deleted records here, as deleted "file_id" records aren't useful. We'll loop over this
                    # again below to get the "CHILD_OF" records, as they might be out of order due to deletions.
                    for item in path_items:
                        # Deleted records have no value
                        if item['value'] == b'':
                            continue

                        # This will find keys that start with a number, rather than letter (ASCII code),
                        # which only matches "file_id" items (from above list of four types).
                        if item['key'][0] < 58:
                            overall_length, ptr = utils.read_int32(item['value'], 0)
                            parent_id, ptr = utils.read_int64(item['value'], ptr)
                            backing_file_path, ptr = utils.read_string(item['value'], ptr)
                            name, ptr = utils.read_string(item['value'], ptr)
                            mod_time, ptr = utils.read_int64(item['value'], ptr)

                            backing_files[item['key'].decode()] = {
                                'modification_time': mod_time,
                                'seq': item['seq'],
                                'state': item['state'],
                                'source_path': item['origin_file']
                            }

                            path_parts = re.split(r'[/\\]', backing_file_path)
                            if path_parts != ['']:
                                normalized_backing_file_path = os.path.join(
                                    path_nodes['0']['fs_path'], fs_type, path_parts[0], path_parts[1])
                                file_exists, file_size, magic_results = self.get_local_file_info(
                                           os.path.join(self.profile_path, normalized_backing_file_path))
                                backing_files[item['key'].decode()]['file_exists'] = file_exists
                                backing_files[item['key'].decode()]['file_size'] = file_size
                                backing_files[item['key'].decode()]['magic_results'] = magic_results

                            else:
                                normalized_backing_file_path = os.path.join(
                                    path_nodes['0']['fs_path'], fs_type, backing_file_path)

                            backing_files[item['key'].decode()]['backing_file_path'] = normalized_backing_file_path

                    # Loop over records again, this time to add to the path_nodes dict (used later to construct
                    # the logical path for items in FileSystem. We look at deleted records here; while the value
                    # is empty, the key still exists and has useful info in it.
                    for item in path_items:
                        if not item['key'].startswith(b'CHILD_OF:'):
                            continue

                        parent, name = item['key'][9:].split(b':')

                        path_node_key = item['value'].decode()
                        if item['value'] == b'':
                            path_node_key = f"deleted-{item['seq']}"

                        path_nodes[path_node_key] = {
                            'name': name.decode(),
                            'type': fs_type,
                            'origin_id': origin_id,
                            'parent': parent.decode(),
                            'fs_path': '',
                            'modification_time': '',
                            'seq': item['seq'],
                            'state': item['state'],
                            'source_path': item['origin_file'],
                            'children': {}
                        }

                        if not item['value'] == b'':
                            value_dict = {
                                'fs_path': backing_files[item['value'].decode()]['backing_file_path'],
                                'modification_time': backing_files[item['value'].decode()]['modification_time'],
                                'file_exists': backing_files[item['value'].decode()].get('file_exists'),
                                'file_size': backing_files[item['value'].decode()].get('file_size'),
                                'magic_results': backing_files[item['value'].decode()].get('magic_results'),
                            }
                            path_nodes[path_node_key].update(value_dict)

                        result_count += 1

                for entry_id in path_nodes:
                    if path_nodes[entry_id].get('parent'):
                        path_nodes[path_nodes[entry_id].get('parent')]['children'][entry_id] = path_nodes[entry_id]
                    else:
                        node_tree[entry_id] = path_nodes[entry_id]

                self.build_logical_fs_path(node_tree['0'])
                flattened_list = []
                self.flatten_nodes_to_list(flattened_list, node_tree['0'])

                for item in flattened_list:
                    result_list.append(Chrome.FileSystemItem(
                        profile=self.profile_path, origin=item.get('origin'), key=item.get('logical_path'),
                        value=item.get('local_path'), seq=item['seq'], state=item['state'],
                        source_path=str(item['source_path']), last_modified=item.get('modification_time'),
                        file_exists=item.get('file_exists'), file_size=item.get('file_size'),
                        magic_results=item.get('magic_results')
                    ))

        log.info(f' - Parsed {len(result_list)} items')
        self.artifacts_counts['File System'] = len(result_list)
        self.parsed_storage.extend(result_list)

    def get_site_characteristics(self, path, dir_name):
        result_list = []

        self.build_md5_hash_list_of_origins()

        log.info('Site Characteristics:')
        sc_root_path = os.path.join(path, dir_name)
        log.info(f' - Reading from {sc_root_path}')

        # Grab listing of 'Site Characteristics' directory
        sc_root_listing = os.listdir(sc_root_path)
        log.debug(f' - {len(sc_root_listing)} files in Site Characteristics directory: {str(sc_root_listing)}')

        items = utils.get_ldb_records(sc_root_path)
        for item in items:
            try:
                from pyhindsight.lib.site_data_pb2 import SiteDataProto

                if item['key'] == b'database_metadata':
                    if item['value'] != b'1':
                        log.warning(f' - Expected type 1; got type {item["value"].encode()}. Trying to parse anyway.')
                    continue

                raw_proto = item['value']

                # Deleted records won't have a value
                if raw_proto:
                    # SiteDataProto built from components/performance_manager/persistence/site_data/site_data.proto
                    parsed_proto = SiteDataProto.FromString(raw_proto)
                    last_loaded = parsed_proto.last_loaded
                else:
                    parsed_proto = ''
                    last_loaded = 0

                matched_url = self.origin_hashes.get(item['key'].decode(), f'MD5 of origin: {item["key"].decode()}')

                sc_record = Chrome.SiteSetting(
                    self.profile_path, url=matched_url, timestamp=utils.to_datetime(last_loaded, self.timezone),
                    key=f'Status: {item["state"]}', value=str(parsed_proto), interpretation='')
                sc_record.row_type += ' (characteristic)'
                result_list.append(sc_record)

            except Exception as e:
                log.exception(f' - Exception parsing SiteDataProto ({item}): {e}')

        log.info(f' - Parsed {len(result_list)} items')
        self.artifacts_counts['Site Characteristics'] = len(result_list)
        self.parsed_artifacts.extend(result_list)

    def get_platform_notifications(self, path, dir_name):
        result_list = []

        try:
            from pyhindsight.lib.notification_database_data_pb2 import NotificationDatabaseDataProto

        except Exception as e:
            log.exception(f' - Exception importing NotificationDatabaseDataProto: {e}')
            self.artifacts_counts['Platform Notifications'] = 'Failed'
            return

        log.info('Platform Notifications:')
        pn_root_path = os.path.join(path, dir_name)
        log.info(f' - Reading from {pn_root_path}')

        # Grab listing of 'Platform Notifications' directory
        pn_root_listing = os.listdir(pn_root_path)
        log.debug(f' - {len(pn_root_listing)} files in Platform Notifications directory: {str(pn_root_listing)}')

        items = utils.get_ldb_records(pn_root_path)
        for item in items:
            # print(item)

            if item['key'] in (b'NEXT_NOTIFICATION_ID'):
                continue

            try:
                key_prefix, key_remainder = item['key'].split(b':', 1)
                assert key_prefix in (b'DATA', b'RESOURCES'), 'Unknown key prefix found for NotificationDatabaseDataProto'

                key_origin, notification_id = key_remainder.split(b'\x00')

                notification_id_type = chr(notification_id[0])
                # assert notification_id_type in ('p', 'n'), 'Type should be [p]ersistent or [n]on-persistent'

                shown_by_browser = False
                if notification_id_type == 'p':
                    if chr(notification_id[1]) == 'b':
                        shown_by_browser = True
                        notification_id_remainder = notification_id[3:]
                    elif chr(notification_id[1]) == '#':
                        notification_id_remainder = notification_id[2:]
                    else:
                        raise ValueError(f'Unknown value in notification id {notification_id}')

                    notification_id_origin, notification_id_remainder = notification_id_remainder.split(b'#',1)
                    id_unique = notification_id_remainder[1:]
                    id_unique_type = int(chr(notification_id_remainder[0]))
                    # print(id_unique)

                output_status = None

                if item['key'].startswith(b'DATA'):
                    raw_proto = item['value']

                else:
                    # continue
                    raw_proto = None

                # Deleted records won't have a value
                if raw_proto:
                    # NotificationDatabaseDataProto built from content/browser/notifications/notification_database_data.proto
                    parsed_proto = NotificationDatabaseDataProto.FromString(raw_proto)
                    time_shown = parsed_proto.notification_data.timestamp
                    output_status = parsed_proto.notification_data.title
                else:
                    parsed_proto = ''
                    time_shown = 0
                    output_status = f"{id_unique} {('notification id', 'developer_tag') [id_unique_type]}"

                pn_record = Chrome.SiteSetting(
                    self.profile_path, url=str(key_origin), timestamp=utils.to_datetime(time_shown, self.timezone),
                    #key=f'Status: {item["state"]}',
                    key=output_status, value=str(parsed_proto), interpretation='')
                pn_record.row_type += ' (notification)'
                result_list.append(pn_record)

            except (AssertionError, ValueError) as e:
                log.exception(f' - Exception while parsing {item}: {e}')


        log.info(f' - Parsed {len(result_list)} items')
        self.artifacts_counts['Platform Notifications'] = len(result_list)
        self.parsed_artifacts.extend(result_list)

    def build_hsts_domain_hashes(self):
        domains = set()
        for artifact in self.parsed_artifacts:
            if isinstance(artifact, self.HistoryItem):
                artifact_url = artifact.url

                if not artifact_url:
                    continue

                # Cookie artifact's "URLs" will be in the form ".example.com",
                # which won't parse, so modify it so it will
                if artifact_url and artifact_url.startswith('.'):
                    artifact_url = 'http://' + artifact_url[1:]

                domain = urllib.parse.urlparse(artifact_url).hostname
                # Some URLs don't have a domain, like local PDF files
                if domain:
                    domains.add(domain)

        for domain in domains:

            # From https://source.chromium.org/chromium/chromium/src/+
            #  /main:net/http/transport_security_state.cc;l=223:
            #   Converts |hostname| from dotted form ("www.google.com") to the form
            #   used in DNS: "\x03www\x06google\x03com", lowercases that, and returns
            #   the result.
            domain_parts = domain.lower().split('.')
            while len(domain_parts) > 1:
                dns_hostname = ''
                for domain_part in domain_parts:
                    dns_hostname += f'{chr(len(domain_part))}{domain_part}'
                dns_hostname += chr(0)

                # From https://source.chromium.org/chromium/chromium/src/+
                #  /main:net/http/transport_security_persister.h;l=103:
                #    The JSON dictionary keys are strings containing
                #    Base64(SHA256(TransportSecurityState::CanonicalizeHost(domain))).
                hashed_domain = base64.b64encode(
                    hashlib.sha256(dns_hostname.encode()).digest()).decode('utf-8')

                # Check if this is new hash (break if not), add it to the dict,
                # and then repeat with the leading domain part removed.
                if hashed_domain in self.hsts_hashes:
                    break
                self.hsts_hashes[hashed_domain] = '.'.join(domain_parts)
                domain_parts = domain_parts[1:]

    def get_transport_security(self, path, dir_name):
        result_list = []

        # Use the URLs from other previously-processed artifacts to generate hashes of domains
        # in the form Chrome uses as the 'host' identifier.
        self.build_hsts_domain_hashes()

        log.info('Transport Security (HSTS):')
        ts_file_path = os.path.join(path, dir_name)
        log.info(f' - Reading from {ts_file_path}')

        # From https://source.chromium.org/chromium/chromium/src/+
        #  /main:net/http/transport_security_persister.h;l=103:
        #    The JSON dictionary keys are strings containing
        #    Base64(SHA256(TransportSecurityState::CanonicalizeHost(domain))).
        #    The reason for hashing them is so that the stored state does not
        #    trivially reveal a user's browsing history to an attacker reading the
        #    serialized state on disk.

        with open(ts_file_path, encoding='utf-8', errors='replace') as f:
            ts_json = json.loads(f.read())

            # As of now (2021), there are two versions of the TransportSecurity JSON file.
            # Version 2 has a top level "version" key (with a value of 2), and version 1
            # has the HSTS domain hashes as top level keys.

            # Version 2
            if ts_json.get('version'):
                assert ts_json['version'] == 2, '"2" is only supported value for "version"'
                hsts = ts_json['sts']

                for item in hsts:
                    if item['host'] in self.hsts_hashes:
                        hsts_domain = self.hsts_hashes[item['host']]
                    else:
                        hsts_domain = f'Encoded domain: {item["host"]}'

                    hsts_record = Chrome.SiteSetting(
                        self.profile_path, url=hsts_domain,
                        timestamp=utils.to_datetime(item['sts_observed'], self.timezone),
                        key='HSTS observed', value=str(item), interpretation='')
                    hsts_record.row_type += ' (hsts)'
                    result_list.append(hsts_record)

            # Version 1
            elif len(ts_json):
                for hashed_domain, domain_settings in ts_json.items():
                    if hashed_domain in self.hsts_hashes:
                        hsts_domain = self.hsts_hashes[hashed_domain]
                    else:
                        hsts_domain = f'{hashed_domain} (encoded domain)'

                    if domain_settings.get('sts_observed'):
                        hsts_record = Chrome.SiteSetting(
                            self.profile_path, url=hsts_domain,
                            timestamp=utils.to_datetime(domain_settings['sts_observed'], self.timezone),
                            key='HSTS observed', value=f'{hashed_domain}: {domain_settings}', interpretation='')
                        hsts_record.row_type += ' (hsts)'
                        result_list.append(hsts_record)

            else:
                log.warning('Unable to process TransportSecurity file; could not determine version.')
                return

        log.info(f' - Parsed {len(result_list)} items')
        self.artifacts_counts['HSTS'] = len(result_list)
        self.parsed_artifacts.extend(result_list)

    def process(self):
        supported_databases = ['History', 'Archived History', 'Media History', 'Web Data', 'Cookies', 'Login Data',
                               'Extension Cookies']
        supported_subdirs = ['Local Storage', 'Extensions', 'File System', 'Platform Notifications', 'Network']
        supported_jsons = ['Bookmarks', 'TransportSecurity']  # , 'Preferences']
        supported_items = supported_databases + supported_subdirs + supported_jsons
        log.debug(f'Supported items: {supported_items}')

        input_listing = os.listdir(self.profile_path)
        for input_file in input_listing:
            # If input_file is in our supported db list, or if the input_file name starts with a
            # value in supported_databases followed by '__' (used to add in dbs from additional sources)
            if input_file in supported_databases or \
                    input_file.startswith(tuple([db + '__' for db in supported_databases])):
                # Process structure from Chrome database files
                self.build_structure(self.profile_path, input_file)

        network_listing = None
        if 'Network' in input_listing:
            network_listing = os.listdir(os.path.join(self.profile_path, 'Network'))
            for input_file in network_listing:
                if input_file in supported_databases or \
                        input_file.startswith(tuple([db + '__' for db in supported_databases])):
                    # Process structure from Chrome database files
                    self.build_structure(self.profile_path, input_file)

        # Use the structure of the input files to determine possible Chrome versions
        self.determine_version()

        if len(self.version) > 1:
            self.display_version = f'{self.version[0]}-{self.version[-1]}'
        elif len(self.version) == 1:
            self.display_version = self.version[0]
        else:
            print('Unable to determine browser version')

        print(self.format_profile_path(self.profile_path))

        print(self.format_processing_output(f'Detected {self.browser_name} version', self.display_version))
        log.info(f'Detected {self.browser_name} version {self.display_version}')

        log.info('Found the following supported files or directories:')
        for input_file in input_listing:
            if input_file in supported_items:
                log.info(f' - {input_file}')

        # Process History files
        custom_type_re = re.compile(r'__([A-z0-9\._]*)$')
        for input_file in input_listing:
            if re.search(r'^History__|^History$', input_file):
                row_type = 'url'
                custom_type_m = re.search(custom_type_re, input_file)
                if custom_type_m:
                    row_type = f'url ({custom_type_m.group(1)})'
                self.get_history(self.profile_path, input_file, self.version, row_type)
                display_type = 'URL' if not custom_type_m else f'URL ({custom_type_m.group(1)})'
                self.artifacts_display[input_file] = f'{display_type} records'
                print(self.format_processing_output(
                    self.artifacts_display[input_file], 
                    self.artifacts_counts.get(input_file, '0')))

                row_type = 'download'
                if custom_type_m:
                    row_type = f'download ({custom_type_m.group(1)})'
                self.get_downloads(self.profile_path, input_file, self.version, row_type)
                display_type = 'Download' if not custom_type_m else f'Download ({custom_type_m.group(1)})'
                self.artifacts_display[input_file + '_downloads'] = f'{display_type} records'
                print(self.format_processing_output(
                    self.artifacts_display[input_file + '_downloads'], 
                    self.artifacts_counts.get(input_file + '_downloads', '0')))

        if 'Archived History' in input_listing:
            self.get_history(self.profile_path, 'Archived History', self.version, 'url (archived)')
            self.artifacts_display['Archived History'] = "Archived URL records"
            print(self.format_processing_output(
                self.artifacts_display['Archived History'],
                self.artifacts_counts.get('Archived History', '0')))

        if 'Media History' in input_listing:
            self.get_media_history(self.profile_path, 'Media History', self.version, 'media (playback end)')
            self.artifacts_display['Media History'] = "Media History records"
            print(self.format_processing_output(
                self.artifacts_display['Media History'],
                self.artifacts_counts.get('Media History', '0')))

        if self.cache_path is not None and self.cache_path != '':
            c_path, c_dir = os.path.split(self.cache_path)
            self.get_cache(c_path, c_dir, row_type='cache')
            self.artifacts_display['Cache'] = 'Cache records'
            print(self.format_processing_output(
                self.artifacts_display['Cache'],
                self.artifacts_counts.get('Cache', '0')))

        elif 'Cache' in input_listing:
            if os.path.isdir(os.path.join(self.profile_path, 'Cache', 'Cache_Data')):
                self.get_cache(os.path.join(self.profile_path, 'Cache'), 'Cache_Data', row_type='cache')
            else:
                self.get_cache(self.profile_path, 'Cache', row_type='cache')
            self.artifacts_display['Cache'] = 'Cache records'
            print(self.format_processing_output(
                self.artifacts_display['Cache'],
                self.artifacts_counts.get('Cache', '0')))
            
        if 'GPUCache' in input_listing:
            self.get_cache(self.profile_path, 'GPUCache', row_type='cache (gpu)')
            self.artifacts_display['GPUCache'] = 'GPU Cache records'
            print(self.format_processing_output(
                self.artifacts_display['GPUCache'],
                self.artifacts_counts.get('GPUCache', '0')))

        if 'Media Cache' in input_listing:
            self.get_cache(self.profile_path, 'Media Cache', row_type='cache (media)')
            self.artifacts_display['Media Cache'] = 'Media Cache records'
            print(self.format_processing_output(
                self.artifacts_display['Media Cache'],
                self.artifacts_counts.get('Media Cache', '0')))

        if 'Application Cache' in input_listing:
            self.get_application_cache(self.profile_path, 'Application Cache', row_type='cache (application)')
            self.artifacts_display['Application Cache'] = 'Application Cache records'
            print(self.format_processing_output(
                self.artifacts_display['Application Cache'],
                self.artifacts_counts.get('Application Cache', '0')))

        if 'Cookies' in input_listing:
            self.get_cookies(self.profile_path, 'Cookies', self.version)
            self.artifacts_display['Cookies'] = 'Cookie records'
            print(self.format_processing_output(
                self.artifacts_display['Cookies'],
                self.artifacts_counts.get('Cookies', '0')))

        if 'Web Data' in input_listing:
            self.get_autofill(self.profile_path, 'Web Data', self.version)
            self.artifacts_display['Autofill'] = 'Autofill records'
            print(self.format_processing_output(
                self.artifacts_display['Autofill'],
                self.artifacts_counts.get('Autofill', '0')))

        if 'Bookmarks' in input_listing:
            self.get_bookmarks(self.profile_path, 'Bookmarks', self.version)
            self.artifacts_display['Bookmarks'] = 'Bookmark records'
            print(self.format_processing_output(
                self.artifacts_display['Bookmarks'],
                self.artifacts_counts.get('Bookmarks', '0')))

        if 'Local Storage' in input_listing:
            self.get_local_storage(self.profile_path, 'Local Storage')
            self.artifacts_display['Local Storage'] = 'Local Storage records'
            print(self.format_processing_output(
                self.artifacts_display['Local Storage'],
                self.artifacts_counts.get('Local Storage', '0')))

        if 'Session Storage' in input_listing:
            self.get_session_storage(self.profile_path, 'Session Storage')
            self.artifacts_display['Session Storage'] = 'Session Storage records'
            print(self.format_processing_output(
                self.artifacts_display['Session Storage'],
                self.artifacts_counts.get('Session Storage', '0')))

        if 'Extensions' in input_listing:
            self.get_extensions(self.profile_path, 'Extensions')
            self.artifacts_display['Extensions'] = 'Extensions'
            print(self.format_processing_output(
                self.artifacts_display['Extensions'],
                self.artifacts_counts.get('Extensions', '0')))

        if 'Extension Cookies' in input_listing:
            # Workaround to cap the version at 65 for Extension Cookies, as until that
            # point it has the same database format as Cookies
            # TODO: Need to revisit this, as in v69 the structures are the same again, but
            # I don't have test data for v67 or v68 to tell when it changed back.
            ext_cookies_version = self.version
            # if min(self.version) > 65:
            #     ext_cookies_version.insert(0, 65)

            self.get_cookies(self.profile_path, 'Extension Cookies', ext_cookies_version)
            self.artifacts_display['Extension Cookies'] = 'Extension Cookie records'
            print(self.format_processing_output(
                self.artifacts_display['Extension Cookies'],
                self.artifacts_counts.get('Extension Cookies', '0')))

        if 'Login Data' in input_listing:
            self.get_login_data(self.profile_path, 'Login Data', self.version)
            self.artifacts_display['Login Data'] = 'Login Data records'
            print(self.format_processing_output(
                self.artifacts_display['Login Data'],
                self.artifacts_counts.get('Login Data', '0')))

        if 'Preferences' in input_listing:
            self.get_preferences(self.profile_path, 'Preferences')
            self.artifacts_display['Preferences'] = 'Preference Items'
            print(self.format_processing_output(
                self.artifacts_display['Preferences'],
                self.artifacts_counts.get('Preferences', '0')))

        if 'Site Characteristics Database' in input_listing:
            self.get_site_characteristics(self.profile_path, 'Site Characteristics Database')
            self.artifacts_display['Site Characteristics'] = "Site Characteristics records"
            print(self.format_processing_output(
                self.artifacts_display['Site Characteristics'],
                self.artifacts_counts.get('Site Characteristics', '0')))

        if 'Platform Notifications' in input_listing:
            self.get_platform_notifications(self.profile_path, 'Platform Notifications')
            self.artifacts_display['Platform Notifications'] = "Platform Notification records"
            print(self.format_processing_output(
                self.artifacts_display['Platform Notifications'],
                self.artifacts_counts.get('Platform Notifications', '0')))

        if 'TransportSecurity' in input_listing:
            self.get_transport_security(self.profile_path, 'TransportSecurity')
            self.artifacts_display['HSTS'] = "HSTS records"
            print(self.format_processing_output(
                self.artifacts_display['HSTS'],
                self.artifacts_counts.get('HSTS', '0')))

        if 'File System' in input_listing:
            self.get_file_system(self.profile_path, 'File System')
            self.artifacts_display['File System'] = 'File System Items'
            print(self.format_processing_output(
                self.artifacts_display['File System'],
                self.artifacts_counts.get('File System', '0')))

        if network_listing:
            if 'Cookies' in network_listing:
                self.get_cookies(os.path.join(self.profile_path, 'Network'), 'Cookies', self.version)
                self.artifacts_display['Cookies'] = 'Cookie records'
                print(self.format_processing_output(
                    self.artifacts_display['Cookies'],
                    self.artifacts_counts.get('Cookies', '0')))

            if 'TransportSecurity' in network_listing:
                self.get_transport_security(os.path.join(self.profile_path, 'Network'), 'TransportSecurity')
                self.artifacts_display['HSTS'] = "HSTS records"
                print(self.format_processing_output(
                    self.artifacts_display['HSTS'],
                    self.artifacts_counts.get('HSTS', '0')))

        # Destroy the cached key so that json serialization doesn't
        # have a cardiac arrest on the non-unicode binary data.
        self.cached_key = None

        self.parsed_artifacts.sort()
        self.parsed_storage.sort()

        # Clean temp directory after processing profile
        if not self.no_copy:
            log.info(f'Deleting temporary directory {self.temp_dir}')
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                log.error(f'Exception deleting temporary directory: {e}')

    class URLItem(WebBrowser.URLItem):
        def __init__(
                self, profile, url_id, url, title, visit_time, last_visit_time, visit_count, typed_count, from_visit,
                transition, hidden, favicon_id, indexed=None, visit_duration=None, visit_source=None,
                transition_friendly=None):
            WebBrowser.URLItem.__init__(
                self, profile=profile, url_id=url_id, url=url, title=title, visit_time=visit_time,
                last_visit_time=last_visit_time, visit_count=visit_count, typed_count=typed_count,
                from_visit=from_visit, transition=transition, hidden=hidden, favicon_id=favicon_id,
                indexed=indexed, visit_duration=visit_duration, visit_source=visit_source,
                transition_friendly=transition_friendly)

        def decode_transition(self):
            # Source: http://src.chromium.org/svn/trunk/src/content/public/common/page_transition_types_list.h
            transition_friendly = {
                0: 'link',                 # User got to this page by clicking a link on another page.
                1: 'typed',                # User got this page by typing the URL in the URL bar.  This should not be
                                           #  used for cases where the user selected a choice that didn't look at all
                                           #  like a URL; see GENERATED below.
                                           # We also use this for other 'explicit' navigation actions.
                2: 'auto bookmark',        # User got to this page through a suggestion in the UI, for example)
                                           #  through the destinations page.
                3: 'auto subframe',        # This is a subframe navigation. This is any content that is automatically
                                           #  loaded in a non-toplevel frame. For example, if a page consists of
                                           #  several frames containing ads, those ad URLs will have this transition
                                           #  type. The user may not even realize the content in these pages is a
                                           #  separate frame, so may not care about the URL (see MANUAL below).
                4: 'manual subframe',      # For subframe navigations that are explicitly requested by the user and
                                           #  generate new navigation entries in the back/forward list. These are
                                           #  probably more important than frames that were automatically loaded in
                                           #  the background because the user probably cares about the fact that this
                                           #  link was loaded.
                5: 'generated',            # User got to this page by typing in the URL bar and selecting an entry
                                           #  that did not look like a URL.  For example, a match might have the URL
                                           #  of a Google search result page, but appear like 'Search Google for ...'.
                                           #  These are not quite the same as TYPED navigations because the user
                                           #  didn't type or see the destination URL.
                                           #  See also KEYWORD.
                6: 'start page',           # This is a toplevel navigation. This is any content that is automatically
                                           #  loaded in a toplevel frame.  For example, opening a tab to show the ASH
                                           #  screen saver, opening the devtools window, opening the NTP after the safe
                                           #  browsing warning, opening web-based dialog boxes are examples of
                                           #  AUTO_TOPLEVEL navigations.
                7: 'form submit',          # The user filled out values in a form and submitted it. NOTE that in
                                           #  some situations submitting a form does not result in this transition
                                           #  type. This can happen if the form uses script to submit the contents.
                8: 'reload',               # The user 'reloaded' the page, either by hitting the reload button or by
                                           #  hitting enter in the address bar.  NOTE: This is distinct from the
                                           #  concept of whether a particular load uses 'reload semantics' (i.e.
                                           #  bypasses cached data).  For this reason, lots of code needs to pass
                                           #  around the concept of whether a load should be treated as a 'reload'
                                           #  separately from their tracking of this transition type, which is mainly
                                           #  used for proper scoring for consumers who care about how frequently a
                                           #  user typed/visited a particular URL.
                                           #  SessionRestore and undo tab close use this transition type too.
                9: 'keyword',              # The url was generated from a replaceable keyword other than the default
                                           #  search provider. If the user types a keyword (which also applies to
                                           #  tab-to-search) in the omnibox this qualifier is applied to the transition
                                           #  type of the generated url. TemplateURLModel then may generate an
                                           #  additional visit with a transition type of KEYWORD_GENERATED against the
                                           #  url 'http://' + keyword. For example, if you do a tab-to-search against
                                           #  wikipedia the generated url has a transition qualifer of KEYWORD, and
                                           #  TemplateURLModel generates a visit for 'wikipedia.org' with a transition
                                           #  type of KEYWORD_GENERATED.
                10: 'keyword generated'    # Corresponds to a visit generated for a keyword. See description of
                                           #  KEYWORD for more details.
            }

            qualifiers_friendly = {
                0x00800000: 'Blocked',                # A managed user attempted to visit a URL but was blocked.
                0x01000000: 'Forward or Back',        # User used the Forward or Back button to navigate among browsing
                                                      #  history.
                0x02000000: 'From Address Bar',       # User used the address bar to trigger this navigation.
                0x04000000: 'Home Page',              # User is navigating to the home page.
                0x08000000: 'From API',               # The transition originated from an external application; the
                                                      #  exact definition of this is embedder dependent.
                0x10000000: 'Navigation Chain Start', # The beginning of a navigation chain.
                0x20000000: 'Navigation Chain End',   # The last transition in a redirect chain.
                0x40000000: 'Client Redirect',        # Redirects caused by JavaScript or a meta refresh tag on the page
                0x80000000: 'Server Redirect'         # Redirects sent from the server by HTTP headers. It might be nice
                                                      #  to break this out into 2 types in the future, permanent or
                                                      #  temporary, if we can get that information from WebKit.
                }

            raw = self.transition
            # If the transition has already been translated to a string, just use that
            if isinstance(raw, str):
                self.transition_friendly = raw
                return

            core_mask = 0xff
            code = raw & core_mask

            if code in list(transition_friendly.keys()):
                self.transition_friendly = transition_friendly[code] + '; '

            for qualifier in qualifiers_friendly:
                if raw & qualifier == qualifier:
                    if not self.transition_friendly:
                        self.transition_friendly = ""
                    self.transition_friendly += qualifiers_friendly[qualifier] + '; '

        def decode_source(self):
            # https://source.chromium.org/chromium/chromium/src/+/master:components/history/core/browser/history_types.h
            source_friendly = {
                0:    'Synced',               # Synchronized from somewhere else.
                1:    'Local',                # User browsed. In my experience, this value isn't written; it will be
                                              # null. See https://cs.chromium.org/chromium/src/components/history/
                None: 'Local',                #  core/browser/visit_database.cc
                2:    'Added by Extension',   # Added by an extension.
                3:    'Firefox (Imported)',
                4:    'IE (Imported)',
                5:    'Safari (Imported)',
                6:    'Chrome/Edge (Imported)',                
                7:    'EdgeHTML (Imported)'}

            raw = self.visit_source

            if raw in list(source_friendly.keys()):
                self.visit_source = source_friendly[raw]

    class DownloadItem(WebBrowser.DownloadItem):
        def __init__(
                self, profile, download_id, url, received_bytes, total_bytes, state, full_path=None, start_time=None,
                end_time=None, target_path=None, current_path=None, opened=None, danger_type=None,
                interrupt_reason=None, etag=None, last_modified=None, chain_index=None, interrupt_reason_friendly=None,
                danger_type_friendly=None, state_friendly=None, status_friendly=None):
            WebBrowser.DownloadItem.__init__(
                self, profile, download_id, url, received_bytes, total_bytes, state, full_path=full_path,
                start_time=start_time, end_time=end_time, target_path=target_path, current_path=current_path,
                opened=opened, danger_type=danger_type, interrupt_reason=interrupt_reason, etag=etag,
                last_modified=last_modified, chain_index=chain_index,
                interrupt_reason_friendly=interrupt_reason_friendly, danger_type_friendly=danger_type_friendly,
                state_friendly=state_friendly, status_friendly=status_friendly)

        def decode_interrupt_reason(self):
            interrupts = {
                0:  'No Interrupt',                # Success

                # from download_interrupt_reason_values.h on Chromium site
                # File errors
                1:  'File Error',                  # Generic file operation failure.
                2:  'Access Denied',               # The file cannot be accessed due to security restrictions.
                3:  'Disk Full',                   # There is not enough room on the drive.
                5:  'Path Too Long',               # The directory or file name is too long.
                6:  'File Too Large',              # The file is too large for the file system to handle.
                7:  'Virus',                       # The file contains a virus.
                10: 'Temporary Problem',           # The file was in use. Too many files are opened at once. We have run
                                                   #  out of memory.
                11: 'Blocked',                     # The file was blocked due to local policy.
                12: 'Security Check Failed',       # An attempt to check the safety of the download failed due to
                                                   #  unexpected reasons. See http://crbug.com/153212.
                13: 'Resume Error',                # An attempt was made to seek past the end of a file in opening a
                                                   #  file (as part of resuming a previously interrupted download).

                # Network errors
                20: 'Network Error',               # Generic network failure.
                21: 'Operation Timed Out',         # The network operation timed out.
                22: 'Connection Lost',             # The network connection has been lost.
                23: 'Server Down',                 # The server has gone down.

                # Server responses
                30: 'Server Error',                # The server indicates that the operation has failed (generic).
                31: 'Range Request Error',         # The server does not support range requests.
                32: 'Server Precondition Error',   # The download request does not meet the specified precondition.
                                                   #  Internal use only:  the file has changed on the server.
                33: 'Unable to get file',          # The server does not have the requested data.
                34: 'Server Unauthorized',         # Server didn't authorize access to resource.
                35: 'Server Certificate Problem',  # Server certificate problem.
                36: 'Server Access Forbidden',     # Server access forbidden.
                37: 'Server Unreachable',          # Unexpected server response. This might indicate that the responding
                                                   #  server may not be the intended server.
                38: 'Content Length Mismatch',     # The server sent fewer bytes than the content-length header. It may
                                                   #  indicate that the connection was closed prematurely, or the
                                                   #  Content-Length header was invalid. The download is only
                                                   #  interrupted if strong validators are present. Otherwise, it is
                                                   #  treated as finished.
                39: 'Cross Origin Redirect',       # An unexpected cross-origin redirect happened.

                # User input
                40: 'Cancelled',                   # The user cancelled the download.
                41: 'Browser Shutdown',            # The user shut down the browser.

                # Crash
                50: 'Browser Crashed'}             # The browser crashed.

            if self.interrupt_reason in list(interrupts.keys()):
                self.interrupt_reason_friendly = interrupts[self.interrupt_reason]
            elif self.interrupt_reason is None:
                self.interrupt_reason_friendly = None
            else:
                self.interrupt_reason_friendly = '[Error - Unknown Interrupt Code]'
                log.error(f' - Error decoding interrupt code for download "{self.url}"')

        def decode_danger_type(self):
            # from download_danger_type.h on Chromium site
            dangers = {
                0: 'Not Dangerous',                 # The download is safe.
                1: 'Dangerous',                     # A dangerous file to the system (eg: a pdf or extension from places
                                                    #  other than gallery).
                2: 'Dangerous URL',                 # Safe Browsing download service shows this URL leads to malicious
                                                    #  file download.
                3: 'Dangerous Content',             # SafeBrowsing download service shows this file content as being
                                                    #  malicious.
                4: 'Content May Be Malicious',      # The content of this download may be malicious (eg: extension is
                                                    #  exe but Safe Browsing has not finished checking the content).
                5: 'Uncommon Content',              # Safe Browsing download service checked the contents of the
                                                    #  download, but didn't have enough data to determine whether 
                                                    #  it was malicious.
                6: 'Dangerous But User Validated',  # The download was evaluated to be one of the other types of danger,
                                                    #  but the user told us to go ahead anyway.
                7: 'Dangerous Host',                # Safe Browsing download service checked the contents of the
                                                    #  download and didn't have data on this specific file, 
                                                    #  but the file was served
                                                    #  from a host known to serve mostly malicious content.
                8: 'Potentially Unwanted',          # Applications and extensions that modify browser and/or computer
                                                    #  settings
                9: 'Allowlisted by Policy',         # Download URL allowed by enterprise policy.
                10: 'Pending Scan',                 # Download is pending a more detailed verdict.
                11: 'Blocked - Password Protected', # Download is password protected, and should be blocked according
                                                    #  to policy.
                12: 'Blocked - Too Large',          # Download is too large, and should be blocked according to policy.
                13: 'Warning - Sensitive Content',  # Download deep scanning identified sensitive content, and
                                                    #  recommended warning the user.
                14: 'Blocked - Sensitive Content',  # Download deep scanning identified sensitive content, and
                                                    #  recommended blocking the file.
                15: 'Safe - Deep Scanned',          # Download deep scanning identified no problems.
                16: 'Dangerous, but user opened',   # Download deep scanning identified a problem, but the file has
                                                    #  already been opened by the user.
                17: 'Prompt for Scanning',          # The user is enrolled in the Advanced Protection Program, and
                                                    #  the server has recommended this file be deep scanned.
                18: 'Blocked - Unsupported Type'   # The download has a file type that is unsupported for deep
                                                    #  scanning, and should be blocked according to policy.
            }

            if self.danger_type in list(dangers.keys()):
                self.danger_type_friendly = dangers[self.danger_type]
            elif self.danger_type is None:
                self.danger_type_friendly = None
            else:
                self.danger_type_friendly = '[Error - Unknown Danger Code]'
                log.error(f' - Error decoding danger code for download "{self.url}"')

        def decode_download_state(self):
            # from download_item.h on Chromium site
            states = {
                0: 'In Progress',   # Download is actively progressing.
                1: 'Complete',      # Download is completely finished.
                2: 'Cancelled',     # Download has been cancelled.
                3: 'Interrupted',   # '3' was the old 'Interrupted' code until a bugfix in Chrome v22. 22+ it's '4'
                4: 'Interrupted'}   # This state indicates that the download has been interrupted.

            if self.state in list(states.keys()):
                self.state_friendly = states[self.state]
            else:
                self.state_friendly = '[Error - Unknown State]'
                log.error(f' - Error decoding download state for download "{self.url}"')

        def create_friendly_status(self):
            try:
                status = "%s -  %i%% [%i/%i]" % \
                         (self.state_friendly, (float(self.received_bytes) / float(self.total_bytes)) * 100,
                          self.received_bytes, self.total_bytes)
            except ZeroDivisionError:
                status = "%s -  %i bytes" % (self.state_friendly, self.received_bytes)
            except:
                status = "[parsing error]"
                log.error(" - Error creating friendly status message for download '{}'".format(self.url))
            self.status_friendly = status

# Cache parsing functionality based on the Chromagnon project (https://github.com/JRBANCEL/Chromagnon) by Jean-Rémy Bancel.
# Modifications done by Ryan Benson (ryan@obsidianforensics.com) for improvements and integration with Hindsight.

# Original copyright notice from Chromagnon:

# Copyright (c) 2012, Jean-Rémy Bancel <jean-remy.bancel@telecom-paristech.org>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the Chromagon Project nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL Jean-Rémy Bancel BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


class CacheAddressError(Exception):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


class CacheAddress():
    """
    Object representing a Chrome Cache Address
    """
    SEPARATE_FILE = 0
    RANKING_BLOCK = 1
    BLOCK_256 = 2
    BLOCK_1024 = 3
    BLOCK_4096 = 4

    typeArray = [("Separate file", 0),
                 ("Ranking block file", 36),
                 ("256 bytes block file", 256),
                 ("1k bytes block file", 1024),
                 ("4k bytes block file", 4096)]

    def __init__(self, uint_32, path):
        """
        Parse the 32 bits of the uint_32
        """
        if uint_32 == 0:
            raise CacheAddressError("Null Address")

        #XXX Is self.binary useful ??
        self.addr = uint_32
        self.path = path

        # Checking that the MSB is set
        self.binary = bin(uint_32)
        if len(self.binary) != 34:
            raise CacheAddressError("Uninitialized Address")

        self.blockType = int(self.binary[3:6], 2)

        # If it is an address of a separate file
        if self.blockType == CacheAddress.SEPARATE_FILE:
            self.fileSelector = "f_%06x" % int(self.binary[6:], 2)
        elif self.blockType == CacheAddress.RANKING_BLOCK:
            self.fileSelector = "data_" + str(int(self.binary[10:18], 2))
        else:
            self.entrySize = CacheAddress.typeArray[self.blockType][1]
            self.contiguousBlock = int(self.binary[8:10], 2)
            self.fileSelector = "data_" + str(int(self.binary[10:18], 2))
            self.blockNumber = int(self.binary[18:], 2)

    def __str__(self):
        string = hex(self.addr) + " ("
        if self.blockType >= CacheAddress.BLOCK_256:
            string += str(self.contiguousBlock) +\
                      " contiguous blocks in "
        string += CacheAddress.typeArray[self.blockType][0] +\
                  " : " + self.fileSelector + ")"
        return string


class CacheData:
    """
    Retrieve data at the given address
    Can save it to a separate file for export
    """

    HTTP_HEADER = 0
    UNKNOWN = 1

    def __init__(self, address, size, isHTTPHeader=False):
        """
        It is a lazy evaluation object : the file is open only if it is
        needed. It can parse the HTTP header if asked to do so.
        See net/http/http_util.cc LocateStartOfStatusLine and
        LocateEndOfHeaders for details.
        """
        self.size = size
        self.address = address
        self.type = CacheData.UNKNOWN

        if isHTTPHeader and self.address.blockType != CacheAddress.SEPARATE_FILE:
            # Getting raw data
            block_bytes = b''
            block = open(os.path.join(self.address.path, self.address.fileSelector), 'rb')

            # Offset in file
            self.offset = 8192 + self.address.blockNumber*self.address.entrySize
            block.seek(self.offset)
            for _ in range(self.size):
                block_bytes += struct.unpack('c', block.read(1))[0]
            block.close()

            # Finding the beginning of the request
            start = re.search(b'HTTP', block_bytes)
            if start is None:
                return
            else:
                block_bytes = block_bytes[start.start():]

            # Finding the end (some null characters : verified by experience)
            end = re.search(b'\x00\x00', block_bytes)
            if end is None:
                return
            else:
                block_bytes = block_bytes[:end.end()-2]

            # Creating the dictionary of headers
            self.headers = {}
            for line in block_bytes.split(b'\0'):
                stripped = line.split(b':')
                self.headers[stripped[0].lower()] = \
                    b':'.join(stripped[1:]).strip()
            self.type = CacheData.HTTP_HEADER

    def save(self, filename=None):
        """Save the data to the specified filename"""
        if self.address.blockType == CacheAddress.SEPARATE_FILE:
            shutil.copy(self.address.path + self.address.fileSelector,
                        filename)
        else:
            output = open(filename, 'wB')
            block = open(self.address.path + self.address.fileSelector, 'rb')
            block.seek(8192 + self.address.blockNumber*self.address.entrySize)
            output.write(block.read(self.size))
            block.close()
            output.close()

    def data(self):
        """Returns a string representing the data"""
        try:
            block = open(os.path.join(self.address.path, self.address.fileSelector), 'rb')
            block.seek(8192 + self.address.blockNumber*self.address.entrySize)
            data = block.read(self.size).decode('utf-8', errors="replace")
            block.close()
        except:
            log.error(" - Error decoding cached URL")
            data = "<error>"
        return data

    def __str__(self):
        """
        Display the type of cacheData
        """
        if self.type == CacheData.HTTP_HEADER:
            if 'content-type' in self.headers:
                return "HTTP Header %s" % self.headers['content-type']
            else:
                return "HTTP Header"
        else:
            return "Data"


class CacheBlock:
    """
    Object representing a block of the cache. It can be the index file or any
    other block type : 256B, 1024B, 4096B, Ranking Block.
    See /net/disk_cache/disk_format.h for details.
    """

    INDEX_MAGIC = 0xC103CAC3
    BLOCK_MAGIC = 0xC104CAC3
    INDEX = 0
    BLOCK = 1

    def __init__(self, filename):
        """
        Parse the header of a cache file
        """
        header = open(filename, 'rb')

        # Read Magic Number
        magic = struct.unpack('I', header.read(4))[0]
        if magic == CacheBlock.BLOCK_MAGIC:
            self.type = CacheBlock.BLOCK
            header.seek(2, 1)
            self.version = struct.unpack('h', header.read(2))[0]
            self.header = struct.unpack('h', header.read(2))[0]
            self.nextFile = struct.unpack('h', header.read(2))[0]
            self.blockSize = struct.unpack('I', header.read(4))[0]
            self.entryCount = struct.unpack('I', header.read(4))[0]
            self.entryMax = struct.unpack('I', header.read(4))[0]
            self.empty = []
            for _ in range(4):
                self.empty.append(struct.unpack('I', header.read(4))[0])
            self.position = []
            for _ in range(4):
                self.position.append(struct.unpack('I', header.read(4))[0])
        elif magic == CacheBlock.INDEX_MAGIC:
            self.type = CacheBlock.INDEX
            header.seek(2, 1)
            self.version = struct.unpack('h', header.read(2))[0]
            self.entryCount = struct.unpack('I', header.read(4))[0]
            self.byteCount = struct.unpack('I', header.read(4))[0]
            self.lastFileCreated = "f_%06x" % struct.unpack('I', header.read(4))[0]
            header.seek(4*2, 1)
            self.tableSize = struct.unpack('I', header.read(4))[0]
        else:
            header.close()
            raise Exception("Invalid Chrome Cache File")
        header.close()


class CacheItem(Chrome.HistoryItem):
    def __init__(self, profile, url, date_created, key, value, http_headers):
        super(CacheItem, self).__init__('cache', timestamp=date_created, profile=profile, name=key, value=value)
        self.profile = profile
        self.url = url
        self.date_created = date_created
        self.key = key
        self.value = value
        self.http_headers = http_headers


class CacheEntry(Chrome.HistoryItem):
    """
    See /net/disk_cache/disk_format.h for details.
    """

    STATE = ["Normal (data cached)",
             "Evicted (data deleted)",
             "Doomed (data to be deleted)"]

    def __init__(self, profile, address, row_type, timezone):
        """
        Parse a Chrome Cache Entry at the given address
        """

        super(CacheEntry, self).__init__(row_type, timestamp=None, profile=profile, name=None, value=None)

        self.profile = profile
        self.httpHeader = None
        self.http_headers_dict = None
        self.timezone = timezone
        block = open(os.path.join(address.path, address.fileSelector), 'rb')

        # Going to the right entry
        block.seek(8192 + address.blockNumber*address.entrySize)

        # Parsing basic fields
        self.hash = struct.unpack('I', block.read(4))[0]
        self.next = struct.unpack('I', block.read(4))[0]
        self.rankingNode = struct.unpack('I', block.read(4))[0]
        self.usageCounter = struct.unpack('I', block.read(4))[0]
        self.reuseCounter = struct.unpack('I', block.read(4))[0]
        self.state = struct.unpack('I', block.read(4))[0]
        self.creationTime = utils.to_datetime(struct.unpack('Q', block.read(8))[0], self.timezone)
        self.keyLength = struct.unpack('I', block.read(4))[0]
        self.keyAddress = struct.unpack('I', block.read(4))[0]

        dataSize = []
        for _ in range(4):
            dataSize.append(struct.unpack('I', block.read(4))[0])

        self.data = []
        for index in range(4):
            addr = struct.unpack('I', block.read(4))[0]
            try:
                addr = CacheAddress(addr, address.path)
                self.data.append(CacheData(addr, dataSize[index], True))
            except CacheAddressError:
                pass

        # Find the HTTP header if there is one
        for data in self.data:
            if data.type == CacheData.HTTP_HEADER:
                self.httpHeader = data
                header_dict = {}
                for header in data.__dict__['headers']:
                    try:
                        header_dict[header.decode('utf-8')] = data.__dict__['headers'][header].decode('utf-8')
                    except:
                        pass
                self.http_headers_dict = header_dict

        self.flags = struct.unpack('I', block.read(4))[0]

        # Skipping pad
        block.seek(5*4, 1)

        # Reading local key
        if self.keyAddress == 0:
            self.key = block.read(self.keyLength).decode('ascii')
        # Key stored elsewhere
        else:
            addr = CacheAddress(self.keyAddress, address.path)

            # It is probably an HTTP header
            self.key = CacheData(addr, self.keyLength, True)

        block.close()

        # Hindsight HistoryItem fields
        self.timestamp = self.creationTime
        self.name = CacheEntry.STATE[self.state]
        self.url = self.keyToStr()
        self.value = ""
        self.etag = ""
        self.server_name = ""
        self.last_modified = ""
        self.file_size = 0
        self.location = ""
        for _ in self.data:
            if _.type != 0:
                self.file_size += _.size
                # Check if we already have an address here; if so, add a text separator
                if len(self.location) > 0:
                    self.location += "; "
                if _.address.blockType == 0:
                    self.location += "{}".format(_.address.fileSelector)
                else:
                    self.location += "{} [{}]".format(_.address.fileSelector, _.offset)

        self.http_headers_str = ""
        if self.http_headers_dict is not None:
            if self.state == 0:
                self.value = "{} ({} bytes)".format(self.http_headers_dict.get('content-type'), self.file_size)
            self.server_name = self.http_headers_dict.get('server')
            self.etag = self.http_headers_dict.get('etag')
            self.last_modified = self.http_headers_dict.get('last-modified')

            for key, value in self.http_headers_dict.items():
                if key and value:
                    self.http_headers_str += "{}: {}\n".format(key, value)
                elif key:
                    self.http_headers_str += "{}\n".format(key)
            self.http_headers_str = self.http_headers_str.rstrip()

    def keyToStr(self):
        """
        Since the key can be a string or a CacheData object, this function is an
        utility to display the content of the key whatever type is it.
        """
        if self.keyAddress == 0:
            return self.key
        else:
            return self.key.data()

    def __str__(self):

        string = "Hash: 0x%08x" % self.hash + '\n'
        if self.__next__ != 0:
            string += "Next: 0x%08x" % self.next + '\n'
        string += "Usage Counter: %d" % self.usageCounter + '\n'\
                  "Reuse Counter: %d" % self.reuseCounter + '\n'\
                  "Creation Time: %s" % self.creationTime + '\n'
        if self.keyAddress != 0:
            string += "Key Address: 0x%08x" % self.keyAddress + '\n'
        string += "Key: %s" % self.key + '\n'
        if self.flags != 0:
            string += "Flags: 0x%08x" % self.flags + '\n'
        string += "State: %s" % CacheEntry.STATE[self.state]
        for data in self.data:
            string += "\nData (%d bytes) at 0x%08x : %s" % (data.size,
                                                            data.address.addr,
                                                            data)
        return string
