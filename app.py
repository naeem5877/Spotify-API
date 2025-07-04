# --- Enhanced Spotify Downloader API for Render ---
from flask import Flask, request, jsonify, send_file, after_this_request
from flask_cors import CORS
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import yt_dlp
import re
import requests
import os
import tempfile
import shutil
import logging
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, TRCK
from mutagen.id3._util import ID3NoHeaderError
import time
import threading
from urllib.parse import quote_plus
import zipfile
import io
import random

# --- Configuration ---
app = Flask(__name__)
CORS(app)
BRANDING_PREFIX = "VibeDownloader - "

# --- Enhanced Logging ---
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

# --- Spotify Configuration ---
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID', '8c4adcd1cebc42eda32054be38a2501f')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', 'de62ea9158714e1ba0c80fd325d21758')

# --- User agents for anti-blocking ---
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0'
]

# --- Initialize Spotify Client ---
def init_spotify():
    try:
        if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
            client_credentials_manager = SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID, 
                client_secret=SPOTIFY_CLIENT_SECRET
            )
            return spotipy.Spotify(client_credentials_manager=client_credentials_manager)
    except Exception as e:
        app.logger.error(f"Spotify initialization failed: {e}")
    return None

sp = init_spotify()

class EnhancedDownloader:
    def __init__(self):
        self.lock = threading.Lock()
        
    def get_ydl_opts(self, use_proxy=False):
        """Get optimized yt-dlp options with anti-blocking measures"""
        opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extractaudio': True,
            'audioformat': 'mp3',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192'  # Lower quality for better reliability
            }],
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'ignoreerrors': True,
            'concurrent_fragments': 1,
            'buffersize': 2048,
            # Anti-blocking headers
            'http_headers': {
                'User-Agent': random.choice(USER_AGENTS),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            },
            # Geo-bypass options
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            # Additional options for stability
            'sleep_interval': 1,
            'max_sleep_interval': 2,
            'writeinfojson': False,
            'writesubtitles': False,
            'writeautomaticsub': False,
        }
        
        # Add proxy if specified
        if use_proxy:
            # You can add proxy configuration here if needed
            pass
            
        return opts

    def detect_spotify_link(self, url):
        try:
            patterns = {
                'track': r'spotify\.com/track/([a-zA-Z0-9]+)',
                'album': r'spotify\.com/album/([a-zA-Z0-9]+)',
                'playlist': r'spotify\.com/playlist/([a-zA-Z0-9]+)'
            }
            for link_type, pattern in patterns.items():
                match = re.search(pattern, url.split('?')[0])
                if match:
                    return {'type': link_type, 'id': match.group(1)}
        except Exception as e:
            app.logger.error(f"URL detection failed: {e}")
        return None

    def download_image(self, image_url):
        try:
            headers = {'User-Agent': random.choice(USER_AGENTS)}
            response = requests.get(image_url, timeout=10, stream=True, headers=headers)
            response.raise_for_status()
            return response.content
        except Exception as e:
            app.logger.warning(f"Image download failed: {e}")
            return None

    def add_metadata(self, file_path, metadata):
        try:
            if not os.path.exists(file_path):
                return False

            time.sleep(0.3)

            try:
                audio_file = ID3(file_path)
            except ID3NoHeaderError:
                audio_file = ID3()
                audio_file.save(file_path)
                audio_file = ID3(file_path)

            # Add metadata
            if metadata.get('title'):
                audio_file['TIT2'] = TIT2(encoding=3, text=metadata['title'])
            if metadata.get('artist'):
                audio_file['TPE1'] = TPE1(encoding=3, text=metadata['artist'])
            if metadata.get('album'):
                audio_file['TALB'] = TALB(encoding=3, text=metadata['album'])

            # Add cover image (smaller size for faster processing)
            if metadata.get('cover_image_data') and len(metadata['cover_image_data']) < 300000:
                audio_file['APIC'] = APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='Cover',
                    data=metadata['cover_image_data']
                )

            audio_file.save(v2_version=3)
            return True
        except Exception as e:
            app.logger.warning(f"Metadata addition failed: {e}")
            return False

    def search_alternative_sources(self, query):
        """Search multiple sources for better reliability"""
        search_queries = [
            f"ytsearch1:{query}",
            f"ytsearch1:{query} official",
            f"ytsearch1:{query} audio",
            f"ytsearch1:{query} song",
        ]
        
        # Alternative search engines (if available)
        alt_queries = [
            f"ytsearch1:{query}",
            # Add more search engines if needed
        ]
        
        return search_queries + alt_queries

    def download_track(self, query, metadata, output_dir, max_attempts=3):
        """Enhanced download with multiple fallback strategies"""
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', f"{metadata['artist']} - {metadata['title']}")
        safe_title = safe_title[:100]  # Shorter for compatibility

        for attempt in range(max_attempts):
            try:
                app.logger.info(f"Download attempt {attempt + 1} for: {query}")
                
                # Try different yt-dlp configurations
                ydl_opts = self.get_ydl_opts(use_proxy=(attempt > 0))
                ydl_opts['outtmpl'] = os.path.join(output_dir, f"{safe_title}.%(ext)s")
                
                # Get search queries with fallbacks
                search_queries = self.search_alternative_sources(query)
                
                with self.lock:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        for search_query in search_queries:
                            try:
                                app.logger.info(f"Trying search: {search_query}")
                                ydl.extract_info(search_query, download=True)
                                break
                            except Exception as search_error:
                                app.logger.warning(f"Search failed: {search_error}")
                                continue
                        else:
                            raise Exception("All search queries failed")

                # Find downloaded file
                expected_file = os.path.join(output_dir, f"{safe_title}.mp3")
                if not os.path.exists(expected_file):
                    # Look for any mp3 file
                    mp3_files = [f for f in os.listdir(output_dir) if f.endswith('.mp3')]
                    if mp3_files:
                        expected_file = os.path.join(output_dir, mp3_files[0])

                if os.path.exists(expected_file):
                    app.logger.info(f"Successfully downloaded: {expected_file}")
                    self.add_metadata(expected_file, metadata)
                    return expected_file
                else:
                    raise Exception("Downloaded file not found")

            except Exception as e:
                app.logger.error(f"Download attempt {attempt + 1} failed: {e}")
                if attempt < max_attempts - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    app.logger.error(f"All download attempts failed for: {query}")

        return None

    def get_track_info(self, track_id):
        if not sp:
            return None
        try:
            track = sp.track(track_id)
            return {
                'id': track['id'],
                'name': track['name'],
                'artists': [artist['name'] for artist in track['artists']],
                'album': track['album']['name'],
                'images': track['album']['images'],
                'track_number': track['track_number']
            }
        except Exception as e:
            app.logger.error(f"Failed to get track info: {e}")
            return None

    def get_album_info(self, album_id):
        if not sp:
            return None
        try:
            album = sp.album(album_id)
            tracks = sp.album_tracks(album_id, limit=20)  # Reduced limit
            return {
                'id': album['id'],
                'name': album['name'],
                'artists': [artist['name'] for artist in album['artists']],
                'images': album['images'],
                'tracks': [{
                    'id': track['id'],
                    'name': track['name'],
                    'artists': [artist['name'] for artist in track['artists']],
                    'album': album['name'],
                    'images': album['images'],
                    'track_number': track['track_number']
                } for track in tracks['items']]
            }
        except Exception as e:
            app.logger.error(f"Failed to get album info: {e}")
            return None

    def get_playlist_info(self, playlist_id):
        if not sp:
            return None
        try:
            playlist = sp.playlist(playlist_id)
            tracks = sp.playlist_tracks(playlist_id, limit=20)  # Reduced limit
            return {
                'id': playlist['id'],
                'name': playlist['name'],
                'images': playlist['images'],
                'tracks': [{
                    'id': item['track']['id'],
                    'name': item['track']['name'],
                    'artists': [artist['name'] for artist in item['track']['artists']],
                    'album': item['track']['album']['name'],
                    'images': item['track']['album']['images'],
                    'track_number': item['track']['track_number']
                } for item in tracks['items'] if item['track'] and item['track']['id']]
            }
        except Exception as e:
            app.logger.error(f"Failed to get playlist info: {e}")
            return None

downloader = EnhancedDownloader()

# --- API Endpoints ---
@app.route('/')
def home():
    return jsonify({
        'message': 'VibeDownloader API - Enhanced for Render',
        'status': 'ok',
        'spotify_available': sp is not None
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})

@app.route('/download')
def download():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400

    if not sp:
        return jsonify({'error': 'Spotify service unavailable'}), 503

    link_info = downloader.detect_spotify_link(url)
    if not link_info:
        return jsonify({'error': 'Invalid Spotify URL'}), 400

    content_type, spotify_id = link_info['type'], link_info['id']
    base_url = request.host_url.rstrip('/')

    try:
        if content_type == 'track':
            track_info = downloader.get_track_info(spotify_id)
            if not track_info:
                return jsonify({'error': 'Track not found'}), 404

            return jsonify({
                'type': 'track',
                'title': track_info['name'],
                'artists': track_info['artists'],
                'album': track_info['album'],
                'thumbnail': track_info['images'][0]['url'] if track_info['images'] else None,
                'download_url': f"{base_url}/stream/{track_info['id']}"
            })

        elif content_type in ['album', 'playlist']:
            info_func = downloader.get_album_info if content_type == 'album' else downloader.get_playlist_info
            item_info = info_func(spotify_id)
            if not item_info:
                return jsonify({'error': f'{content_type} not found'}), 404

            return jsonify({
                'type': content_type,
                'title': item_info['name'],
                'thumbnail': item_info['images'][0]['url'] if item_info['images'] else None,
                'total_tracks': len(item_info['tracks']),
                'tracks': [{
                    'title': track['name'],
                    'artists': track['artists'],
                    'download_url': f"{base_url}/stream/{track['id']}"
                } for track in item_info['tracks']],
                'zip_url': f"{base_url}/zip/{content_type}/{spotify_id}"
            })

    except Exception as e:
        app.logger.error(f"Download endpoint error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/stream/<track_id>')
def stream_track(track_id):
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        app.logger.info(f"Starting download for track: {track_id}")

        track_info = downloader.get_track_info(track_id)
        if not track_info:
            return jsonify({'error': 'Track not found'}), 404

        metadata = {
            'title': track_info['name'],
            'artist': ', '.join(track_info['artists']),
            'album': track_info['album']
        }

        # Add cover image for single tracks
        if track_info.get('images'):
            metadata['cover_image_data'] = downloader.download_image(track_info['images'][0]['url'])

        # Enhanced query with multiple variations
        queries = [
            f"{metadata['artist']} - {metadata['title']}",
            f"{metadata['artist']} {metadata['title']}",
            f"{metadata['title']} {metadata['artist']}",
            f"{metadata['title']}"
        ]

        downloaded_file = None
        for query in queries:
            app.logger.info(f"Trying query: {query}")
            downloaded_file = downloader.download_track(query, metadata, temp_dir)
            if downloaded_file:
                break

        if not downloaded_file:
            app.logger.error(f"Download failed for track {track_id}")
            return jsonify({'error': 'Download failed - content may be restricted'}), 500

        filename = f"{BRANDING_PREFIX}{metadata['artist']} - {metadata['title']}.mp3"
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)

        @after_this_request
        def cleanup(response):
            try:
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception as e:
                app.logger.warning(f"Cleanup failed: {e}")
            return response

        return send_file(
            downloaded_file,
            as_attachment=True,
            download_name=filename,
            mimetype='audio/mpeg'
        )

    except Exception as e:
        app.logger.error(f"Stream endpoint error: {e}")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': 'Server error'}), 500

@app.route('/zip/<item_type>/<item_id>')
def download_zip(item_type, item_id):
    if item_type not in ['album', 'playlist']:
        return jsonify({'error': 'Invalid type'}), 400

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        app.logger.info(f"Starting ZIP download for {item_type}: {item_id}")

        info_func = downloader.get_album_info if item_type == 'album' else downloader.get_playlist_info
        item_info = info_func(item_id)
        if not item_info:
            return jsonify({'error': f'{item_type} not found'}), 404

        zip_buffer = io.BytesIO()
        successful_downloads = 0

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Limit tracks for free tier
            max_tracks = 5 if item_type == 'playlist' else 10
            for i, track in enumerate(item_info['tracks'][:max_tracks]):
                try:
                    app.logger.info(f"Processing track {i+1}/{min(len(item_info['tracks']), max_tracks)}: {track['name']}")
                    
                    metadata = {
                        'title': track['name'],
                        'artist': ', '.join(track['artists']),
                        'album': track['album']
                    }

                    # Try multiple query variations
                    queries = [
                        f"{metadata['artist']} - {metadata['title']}",
                        f"{metadata['artist']} {metadata['title']}",
                        f"{metadata['title']}"
                    ]

                    downloaded_file = None
                    for query in queries:
                        downloaded_file = downloader.download_track(query, metadata, temp_dir)
                        if downloaded_file:
                            break

                    if downloaded_file and os.path.exists(downloaded_file):
                        zip_filename = f"{i+1:02d} - {metadata['artist']} - {metadata['title']}.mp3"
                        zip_filename = re.sub(r'[<>:"/\\|?*]', '_', zip_filename)
                        zip_file.write(downloaded_file, zip_filename)
                        os.remove(downloaded_file)
                        successful_downloads += 1
                        app.logger.info(f"Successfully added to ZIP: {zip_filename}")
                    else:
                        app.logger.warning(f"Failed to download: {track['name']}")

                except Exception as track_error:
                    app.logger.error(f"Error processing track {track['name']}: {track_error}")
                    continue

        if successful_downloads == 0:
            return jsonify({'error': 'No tracks could be downloaded'}), 500

        zip_buffer.seek(0)
        app.logger.info(f"ZIP created successfully with {successful_downloads} tracks")

        @after_this_request
        def cleanup(response):
            try:
                if temp_dir:
                    shutil.rmtree(temp_dir)
            except Exception as e:
                app.logger.warning(f"Cleanup failed: {e}")
            return response

        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f"{BRANDING_PREFIX}{item_info['name']}.zip",
            mimetype='application/zip'
        )

    except Exception as e:
        app.logger.error(f"ZIP endpoint error: {e}")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': 'ZIP creation failed'}), 500

# --- Error Handlers ---
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(503)
def service_unavailable(error):
    return jsonify({'error': 'Service temporarily unavailable'}), 503

# --- Production Entry Point ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
