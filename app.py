# --- Enhanced Spotify Downloader API 2025 - Latest Fixes ---
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
import subprocess
import json
from datetime import datetime

# --- Configuration ---
app = Flask(__name__)
CORS(app)
BRANDING_PREFIX = "VibeDownloader - "

# --- Logging ---
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

# --- Spotify Configuration ---
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID', '8c4adcd1cebc42eda32054be38a2501f')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', 'de62ea9158714e1ba0c80fd325d21758')

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
        app.logger.error(f"Spotify init error: {e}")
    return None

sp = init_spotify()

class EnhancedDownloader:
    def __init__(self):
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0'
        ]

        # --- OPTIMIZED ydl_opts for production environments ---
        self.ydl_base_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extractaudio': True,
            'audioformat': 'mp3',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192'
            }],
            'socket_timeout': 30, # Reduced timeout for faster fails
            'retries': 2, # Reduced retries
            'fragment_retries': 2, # Reduced retries
            'ignoreerrors': True,
            'geo_bypass': True,
            'nocheckcertificate': True,
            'rm_cachedir': True,
            'outtmpl': '%(title)s.%(ext)s', # Default template
        }
        self.lock = threading.Lock()

    def get_ydl_opts(self):
        """Get yt-dlp options with random user agent and PROXY SUPPORT."""
        opts = self.ydl_base_opts.copy()
        
        # Randomize user agent
        selected_ua = random.choice(self.user_agents)
        opts['http_headers'] = {'User-Agent': selected_ua}
        
        # --- CRITICAL FIX FOR RENDER/HOSTING: Use a proxy ---
        proxy = os.getenv('PROXY_URL')
        if proxy:
            app.logger.info("Using proxy for yt-dlp.")
            opts['proxy'] = proxy
        else:
            app.logger.warning("No PROXY_URL found. Downloads on hosting platforms may fail.")

        # Ensure source_address is not set on containerized platforms
        opts['source_address'] = '0.0.0.0' # Use 0.0.0.0 to let OS choose interface
            
        return opts

    def detect_spotify_link(self, url):
        patterns = {
            'track': r'spotify\.com/track/([a-zA-Z0-9]+)',
            'album': r'spotify\.com/album/([a-zA-Z0-9]+)',
            'playlist': r'spotify\.com/playlist/([a-zA-Z0-9]+)'
        }
        for link_type, pattern in patterns.items():
            match = re.search(pattern, url.split('?')[0])
            if match:
                return {'type': link_type, 'id': match.group(1)}
        return None

    def download_image(self, image_url):
        try:
            headers = {'User-Agent': random.choice(self.user_agents)}
            response = requests.get(image_url, timeout=15, stream=True, headers=headers)
            response.raise_for_status()
            return response.content
        except Exception as e:
            app.logger.error(f"Image download error: {e}")
            return None

    def add_metadata(self, file_path, metadata):
        try:
            if not os.path.exists(file_path): return False
            time.sleep(0.2)  # Short delay for file handles
            audio_file = MP3(file_path, ID3=ID3)
            if audio_file.tags is None:
                audio_file.add_tags()
            
            audio_file.tags['TIT2'] = TIT2(encoding=3, text=metadata.get('title', ''))
            audio_file.tags['TPE1'] = TPE1(encoding=3, text=metadata.get('artist', ''))
            audio_file.tags['TALB'] = TALB(encoding=3, text=metadata.get('album', ''))
            if metadata.get('year'):
                audio_file.tags['TDRC'] = TDRC(encoding=3, text=str(metadata['year']))
            if metadata.get('track_number'):
                audio_file.tags['TRCK'] = TRCK(encoding=3, text=str(metadata['track_number']))
            
            if metadata.get('cover_image_data'):
                audio_file.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=metadata['cover_image_data']))
            
            audio_file.save(v2_version=3)
            return True
        except Exception as e:
            app.logger.error(f"Metadata error for {file_path}: {e}")
            return False

    def get_alternative_sources(self, query):
        """Prioritize SoundCloud then YouTube to avoid IP blocks."""
        return [
            f"scsearch1:{query}",  # Search SoundCloud first
            f"ytsearch1:{query} audio", # More specific YouTube search
            f"ytsearch1:{query}" # Fallback YouTube search
        ]

    def download_track(self, query, metadata, output_dir):
        """Main download method with enhanced error handling"""
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', f"{metadata['artist']} - {metadata['title']}")[:100]
        search_query = f"{metadata['artist']} {metadata['title']}"
        sources = self.get_alternative_sources(search_query)

        for source in sources:
            try:
                app.logger.info(f"Attempting source: {source}")
                ydl_opts = self.get_ydl_opts()
                # Use a unique temp name to avoid conflicts
                temp_filename = f"{safe_title}_{random.randint(1000, 9999)}"
                ydl_opts['outtmpl'] = os.path.join(output_dir, f"{temp_filename}.%(ext)s")

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(source, download=True) # Download directly
                    
                    if not info or ('entries' in info and not info['entries']):
                        continue

                    # Find the downloaded file
                    for file in os.listdir(output_dir):
                        if file.startswith(temp_filename) and file.endswith('.mp3'):
                            downloaded_file = os.path.join(output_dir, file)
                            final_file = os.path.join(output_dir, f"{safe_title}.mp3")
                            
                            # Ensure no old file exists
                            if os.path.exists(final_file):
                                os.remove(final_file)
                                
                            shutil.move(downloaded_file, final_file)
                            
                            if self.add_metadata(final_file, metadata):
                                app.logger.info(f"✓ Success with source '{source}' for: {query}")
                                return final_file
                            else:
                                app.logger.warning(f"Metadata failed but file exists: {final_file}")
                                return final_file # Return even if metadata fails

            except Exception as e:
                # Catch yt-dlp's DownloadError specifically if needed
                if isinstance(e, yt_dlp.utils.DownloadError):
                    app.logger.warning(f"Source '{source}' failed: {e.args[0]}")
                else:
                    app.logger.error(f"Download process error with source '{source}': {e}")
                continue
        
        app.logger.error(f"❌ All download attempts failed for: {query}")
        return None

    def get_track_info(self, track_id):
        if not sp: return None
        try:
            track = sp.track(track_id)
            return {
                'id': track['id'],
                'name': track['name'],
                'artists': [artist['name'] for artist in track['artists']],
                'album': track['album']['name'],
                'images': track['album']['images'],
                'track_number': track['track_number'],
                'release_date': track['album'].get('release_date', ''),
            }
        except Exception as e:
            app.logger.error(f"Error getting track info for {track_id}: {e}")
            return None

    # ... get_album_info and get_playlist_info remain the same ...
    # ... but let's limit album/playlist downloads even more for stability on Render ...
    def get_album_info(self, album_id):
        if not sp: return None
        try:
            album = sp.album(album_id)
            tracks = sp.album_tracks(album_id, limit=50) # Fetch up to 50
            return {
                'id': album['id'],
                'name': album['name'],
                'artists': [artist['name'] for artist in album['artists']],
                'images': album['images'],
                'release_date': album.get('release_date', ''),
                'tracks': [{
                    'id': track['id'],
                    'name': track['name'],
                    'artists': [artist['name'] for artist in track['artists']],
                    'album': album['name'],
                    'images': album['images'],
                    'track_number': track['track_number'],
                } for track in tracks['items'][:10] if track['id']]  # Limit to first 10 for ZIP
            }
        except Exception as e:
            app.logger.error(f"Error getting album info: {e}")
            return None

    def get_playlist_info(self, playlist_id):
        if not sp: return None
        try:
            playlist = sp.playlist(playlist_id)
            tracks = sp.playlist_tracks(playlist_id, limit=50) # Fetch up to 50
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
                    'track_number': item['track']['track_number'],
                } for item in tracks['items'][:10] if item.get('track') and item['track'].get('id')]  # Limit to first 10 for ZIP
            }
        except Exception as e:
            app.logger.error(f"Error getting playlist info: {e}")
            return None

# --- API Endpoints (mostly unchanged, just added logging) ---
@app.route('/')
def home():
    return jsonify({'message': 'VibeDownloader API - 2025 Render Edition', 'status': 'online'})

@app.route('/download')
def download():
    url = request.args.get('url')
    if not url: return jsonify({'error': 'URL parameter required'}), 400
    if not sp: return jsonify({'error': 'Spotify service unavailable'}), 503

    link_info = downloader.detect_spotify_link(url)
    if not link_info: return jsonify({'error': 'Invalid or unsupported Spotify URL'}), 400
    # ... rest of the endpoint is fine
    content_type, spotify_id = link_info['type'], link_info['id']
    base_url = request.host_url.rstrip('/')

    try:
        if content_type == 'track':
            track_info = downloader.get_track_info(spotify_id)
            if not track_info: return jsonify({'error': 'Track not found or unavailable'}), 404
            return jsonify({
                'type': 'track', 'title': track_info['name'], 'artists': track_info['artists'],
                'album': track_info['album'],
                'thumbnail': track_info['images'][0]['url'] if track_info.get('images') else None,
                'download_url': f"{base_url}/download/stream/{track_info['id']}"
            })
        elif content_type in ['album', 'playlist']:
            info_func = downloader.get_album_info if content_type == 'album' else downloader.get_playlist_info
            item_info = info_func(spotify_id)
            if not item_info: return jsonify({'error': f'{content_type.capitalize()} not found or unavailable'}), 404
            return jsonify({
                'type': content_type, 'title': item_info['name'],
                'thumbnail': item_info['images'][0]['url'] if item_info.get('images') else None,
                'total_tracks': len(item_info['tracks']),
                'tracks': [{
                    'title': track['name'], 'artists': track['artists'],
                    'download_url': f"{base_url}/download/stream/{track['id']}"
                } for track in item_info['tracks']],
                'zip_url': f"{base_url}/download/zip/{content_type}/{spotify_id}"
            })
    except Exception as e:
        app.logger.error(f"Download endpoint error: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/download/stream/<track_id>')
def stream_track(track_id):
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        app.logger.info(f"🎵 Starting download for track: {track_id} in dir {temp_dir}")
        track_info = downloader.get_track_info(track_id)
        if not track_info: return jsonify({'error': 'Track not found in Spotify'}), 404
        
        year = int(track_info['release_date'][:4]) if track_info.get('release_date') else None
        metadata = {
            'title': track_info['name'], 'artist': ', '.join(track_info['artists']),
            'album': track_info['album'], 'track_number': track_info.get('track_number', 1),
            'year': year,
            'cover_image_data': downloader.download_image(track_info['images'][0]['url']) if track_info.get('images') else None
        }
        query = f"{metadata['artist']} {metadata['title']}"
        app.logger.info(f"🔍 Searching for: {query}")
        downloaded_file = downloader.download_track(query, metadata, temp_dir)
        
        if not downloaded_file or not os.path.exists(downloaded_file):
            app.logger.error(f"❌ Download failed for track {track_id}")
            shutil.rmtree(temp_dir)
            return jsonify({'error': 'Download failed - could not find audio source'}), 500
        
        filename = f"{BRANDING_PREFIX}{metadata['artist']} - {metadata['title']}.mp3"
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)[:200]

        @after_this_request
        def cleanup(response):
            try:
                shutil.rmtree(temp_dir)
                app.logger.info(f"🧹 Cleaned up temp dir: {temp_dir}")
            except Exception as e:
                app.logger.error(f"Error during cleanup: {e}")
            return response

        app.logger.info(f"✅ Successfully processed track: {track_id}, sending file.")
        return send_file(downloaded_file, as_attachment=True, download_name=filename, mimetype='audio/mpeg')

    except Exception as e:
        app.logger.error(f"❌ Stream track error: {e}", exc_info=True)
        if temp_dir: shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': 'Internal server error during download process'}), 500

# The /download/zip endpoint can largely remain the same, but will benefit from the robust download_track method.
# ... (The rest of your code, including zip download and error handlers, is fine) ...
@app.route('/download/zip/<item_type>/<item_id>')
def download_zip(item_type, item_id):
    if item_type not in ['album', 'playlist']:
        return jsonify({'error': 'Invalid type - must be album or playlist'}), 400

    temp_dir = tempfile.mkdtemp()
    try:
        app.logger.info(f"📦 Starting ZIP download for {item_type}: {item_id}")

        info_func = downloader.get_album_info if item_type == 'album' else downloader.get_playlist_info
        item_info = info_func(item_id)
        if not item_info: return jsonify({'error': f'{item_type.capitalize()} not found'}), 404

        zip_buffer = io.BytesIO()
        successful_downloads = 0
        total_tracks = len(item_info['tracks'])

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for i, track in enumerate(item_info['tracks']):
                try:
                    app.logger.info(f"📥 Processing track {i+1}/{total_tracks} for ZIP: {track['name']}")
                    year = int(item_info['release_date'][:4]) if item_info.get('release_date') else None
                    metadata = {
                        'title': track['name'], 'artist': ', '.join(track['artists']),
                        'album': track['album'], 'track_number': track.get('track_number', i+1), 'year': year
                    }
                    query = f"{metadata['artist']} {metadata['title']}"
                    downloaded_file = downloader.download_track(query, metadata, temp_dir)

                    if downloaded_file and os.path.exists(downloaded_file):
                        zip_filename = f"{i+1:02d} - {metadata['artist']} - {metadata['title']}.mp3"
                        zip_filename = re.sub(r'[<>:"/\\|?*]', '_', zip_filename)[:150]
                        zip_file.write(downloaded_file, zip_filename)
                        os.remove(downloaded_file)
                        successful_downloads += 1
                        app.logger.info(f"✅ Added track {i+1} to ZIP")
                    else:
                        app.logger.warning(f"⚠️ Failed to download track {i+1} for ZIP: {track['name']}")

                except Exception as e:
                    app.logger.error(f"❌ Error processing track {i+1} for ZIP: {e}")
                    continue
        
        if successful_downloads == 0:
            shutil.rmtree(temp_dir)
            return jsonify({'error': 'No tracks could be downloaded successfully for the ZIP file'}), 500

        zip_buffer.seek(0)
        
        @after_this_request
        def cleanup(response):
            try:
                shutil.rmtree(temp_dir)
            except: pass
            return response

        zip_filename = f"{BRANDING_PREFIX}{item_info['name']}.zip"
        zip_filename = re.sub(r'[<>:"/\\|?*]', '_', zip_filename)[:200]
        return send_file(zip_buffer, as_attachment=True, download_name=zip_filename, mimetype='application/zip')

    except Exception as e:
        app.logger.error(f"❌ ZIP download error: {e}", exc_info=True)
        if temp_dir: shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': 'ZIP creation failed due to an internal error'}), 500


# --- Error Handlers (Unchanged) ---
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found', 'message': 'The requested resource does not exist'}), 404

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f"Internal server error: {error}", exc_info=True)
    return jsonify({'error': 'Internal server error', 'message': 'Something went wrong on our end'}), 500

# --- Production Entry Point ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.logger.info(f"🚀 Starting VibeDownloader API on port {port}")
    # For local testing, you might not have gunicorn. Use this instead.
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
