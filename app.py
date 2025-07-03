# --- Imports ---
from flask import Flask, request, jsonify, send_file, after_this_request, url_for
from flask_cors import CORS
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import yt_dlp
import re
import requests
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile
import shutil
import logging
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, TRCK
from mutagen.id3._util import ID3NoHeaderError
import time
import threading
from urllib.parse import quote_plus

# --- Application Configuration ---
app = Flask(__name__)
CORS(app)
BRANDING_PREFIX = "VibeDownloader.me - "

# --- Enhanced Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)
else:
    app.logger.setLevel(logging.INFO)

# --- Spotify API Configuration ---
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID', '8c4adcd1cebc42eda32054be38a2501f')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', 'de62ea9158714e1ba0c80fd325d21758')

# --- Initialize Spotify Client with retry logic ---
def initialize_spotify_client():
    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
        app.logger.warning("Spotify credentials not found in environment variables.")
        return None

    max_retries = 3
    for attempt in range(max_retries):
        try:
            client_credentials_manager = SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID, 
                client_secret=SPOTIFY_CLIENT_SECRET
            )
            sp_client = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
            # Test the connection
            sp_client.search(q='test', type='track', limit=1)
            app.logger.info("Spotify client initialized successfully")
            return sp_client
        except Exception as e:
            app.logger.error(f"Failed to initialize Spotify client (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
    return None

sp = initialize_spotify_client()

class SpotifyDownloader:
    def __init__(self):
        # Enhanced yt-dlp options for better reliability on Render
        self.ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'noplaylist': True,
            'quiet': False,
            'no_warnings': False,
            'extractaudio': True,
            'audioformat': 'mp3',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192'  # Reduced quality for faster processing on free tier
            }],
            'socket_timeout': 20,  # Reduced timeout for free tier
            'retries': 2,  # Reduced retries
            'fragment_retries': 2,
            'ignoreerrors': False,
            'cookiefile': None,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'max_filesize': 50 * 1024 * 1024,  # 50MB limit for free tier
        }
        self.lock = threading.Lock()

    def detect_spotify_link(self, url):
        try:
            cleaned_url = url.split('?')[0]
            patterns = {
                'track': r'spotify\.com/track/([a-zA-Z0-9]+)',
                'album': r'spotify\.com/album/([a-zA-Z0-9]+)',
                'playlist': r'spotify\.com/playlist/([a-zA-Z0-9]+)'
            }
            for link_type, pattern in patterns.items():
                match = re.search(pattern, cleaned_url)
                if match:
                    return {'type': link_type, 'id': match.group(1)}
            return None
        except Exception as e:
            app.logger.error(f"Error detecting Spotify link: {e}")
            return None

    def download_image(self, image_url, max_retries=2):
        for attempt in range(max_retries):
            try:
                response = requests.get(
                    image_url, 
                    timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                )
                response.raise_for_status()
                return response.content
            except Exception as e:
                app.logger.error(f"Error downloading image (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
        return None

    def add_metadata_to_file(self, file_path, metadata):
        max_retries = 2
        for attempt in range(max_retries):
            try:
                if not os.path.exists(file_path):
                    app.logger.error(f"File does not exist: {file_path}")
                    return False

                time.sleep(0.5)

                try:
                    audio_file = ID3(file_path)
                except ID3NoHeaderError:
                    audio_file = ID3()
                    audio_file.save(file_path)
                    audio_file = ID3(file_path)

                if metadata.get('title'):
                    audio_file['TIT2'] = TIT2(encoding=3, text=metadata['title'])
                if metadata.get('artist'):
                    audio_file['TPE1'] = TPE1(encoding=3, text=metadata['artist'])
                if metadata.get('album'):
                    audio_file['TALB'] = TALB(encoding=3, text=metadata['album'])
                if metadata.get('date'):
                    audio_file['TDRC'] = TDRC(encoding=3, text=metadata['date'])
                if metadata.get('track'):
                    audio_file['TRCK'] = TRCK(encoding=3, text=str(metadata['track']))
                if metadata.get('cover_image_data'):
                    audio_file['APIC'] = APIC(
                        encoding=3,
                        mime='image/jpeg',
                        type=3,
                        desc='Cover',
                        data=metadata['cover_image_data']
                    )

                audio_file.save(v2_version=3)
                app.logger.info(f"Successfully added metadata to {file_path}")
                return True

            except Exception as e:
                app.logger.error(f"Error adding metadata (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)

        return False

    def search_and_download_youtube(self, query, metadata, output_dir, max_retries=2):
        for attempt in range(max_retries):
            try:
                app.logger.info(f"Attempting download for query: {query} (attempt {attempt + 1})")

                ydl_opts = self.ydl_opts.copy()

                branded_title = f"{BRANDING_PREFIX}{metadata['artist']} - {metadata['title']}"
                safe_filename = re.sub(r'[<>:"/\\|?*]', '_', branded_title)
                safe_filename = safe_filename[:150]  # Shorter for free tier

                ydl_opts['outtmpl'] = os.path.join(output_dir, f"{safe_filename}.%(ext)s")

                with self.lock:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        try:
                            search_query = f"ytsearch1:{query}"
                            app.logger.info(f"Searching YouTube for: {search_query}")
                            ydl.extract_info(search_query, download=True)
                        except Exception as download_error:
                            app.logger.error(f"yt-dlp download error: {download_error}")
                            raise

                expected_file = os.path.join(output_dir, f"{safe_filename}.mp3")

                if not os.path.exists(expected_file):
                    mp3_files = [f for f in os.listdir(output_dir) if f.endswith('.mp3')]
                    if mp3_files:
                        newest_file = max(mp3_files, key=lambda x: os.path.getctime(os.path.join(output_dir, x)))
                        expected_file = os.path.join(output_dir, newest_file)
                        app.logger.info(f"Using file: {expected_file}")

                if os.path.exists(expected_file):
                    if self.add_metadata_to_file(expected_file, metadata):
                        app.logger.info(f"Successfully downloaded and processed: {expected_file}")
                        return expected_file
                    else:
                        app.logger.warning(f"Downloaded file but failed to add metadata: {expected_file}")
                        return expected_file
                else:
                    app.logger.error(f"Downloaded file not found: {expected_file}")
                    try:
                        files_in_dir = os.listdir(output_dir)
                        app.logger.info(f"Files in output directory: {files_in_dir}")
                    except:
                        pass

            except Exception as e:
                app.logger.error(f"YouTube download failed for '{query}' (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        app.logger.error(f"All download attempts failed for: {query}")
        return None

    def prepare_metadata(self, track_info, include_image_data=False):
        metadata = {
            'title': track_info.get('name', ''),
            'artist': ', '.join(track_info.get('artists', [])),
            'album': track_info.get('album', ''),
            'date': track_info.get('release_date', ''),
            'track': track_info.get('track_number', 1)
        }

        if track_info.get('images') and track_info['images']:
            metadata['cover_image_url'] = track_info['images'][0]['url']
            if include_image_data:
                metadata['cover_image_data'] = self.download_image(metadata['cover_image_url'])

        return metadata

    def get_track_info(self, track_id, max_retries=2):
        if not sp:
            return None

        for attempt in range(max_retries):
            try:
                track = sp.track(track_id)
                return {
                    'id': track['id'],
                    'name': track['name'],
                    'artists': [artist['name'] for artist in track['artists']],
                    'album': track['album']['name'],
                    'release_date': track['album']['release_date'],
                    'images': track['album']['images'],
                    'track_number': track['track_number']
                }
            except Exception as e:
                app.logger.error(f"Error fetching track {track_id} (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
        return None

    def get_album_info(self, album_id, max_retries=2):
        if not sp:
            return None

        for attempt in range(max_retries):
            try:
                album = sp.album(album_id)
                tracks = sp.album_tracks(album_id)
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
                        'release_date': album['release_date'],
                        'images': album['images'],
                        'track_number': track['track_number']
                    } for track in tracks['items']]
                }
            except Exception as e:
                app.logger.error(f"Error fetching album {album_id} (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
        return None

    def get_playlist_info(self, playlist_id, max_retries=2):
        if not sp:
            return None

        for attempt in range(max_retries):
            try:
                playlist = sp.playlist(playlist_id)
                tracks = sp.playlist_tracks(playlist_id)
                return {
                    'id': playlist['id'],
                    'name': playlist['name'],
                    'owner': playlist['owner']['display_name'],
                    'images': playlist['images'],
                    'tracks': [{
                        'id': item['track']['id'],
                        'name': item['track']['name'],
                        'artists': [artist['name'] for artist in item['track']['artists']],
                        'album': item['track']['album']['name'],
                        'release_date': item['track']['album']['release_date'],
                        'images': item['track']['album']['images'],
                        'track_number': item['track']['track_number']
                    } for item in tracks['items'] if item['track'] and item['track']['id']]
                }
            except Exception as e:
                app.logger.error(f"Error fetching playlist {playlist_id} (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
        return None

downloader = SpotifyDownloader()

# --- API Endpoints ---
@app.route('/')
def home():
    return jsonify({
        'message': 'VibeDownloader.me API',
        'version': '1.1 Render Enhanced',
        'status': 'operational'
    })

@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'spotify_connected': sp is not None,
        'timestamp': time.time()
    })

@app.route('/download')
def download():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL parameter is required'}), 400

    if not sp:
        return jsonify({'error': 'Spotify service is not configured on the server.'}), 503

    link_info = downloader.detect_spotify_link(url)
    if not link_info:
        return jsonify({'error': 'Invalid or unsupported Spotify URL'}), 400

    content_type, spotify_id = link_info['type'], link_info['id']
    base_url = request.host_url.rstrip('/')

    try:
        if content_type == 'track':
            track_info = downloader.get_track_info(spotify_id)
            if not track_info:
                return jsonify({'error': 'Track not found'}), 404

            return jsonify({
                'type': 'track',
                'title': track_info.get('name'),
                'artists': track_info.get('artists'),
                'album_name': track_info.get('album'),
                'thumbnail_url': track_info['images'][0]['url'] if track_info.get('images') else None,
                'download_link': f"{base_url}{url_for('stream_file', track_id=track_info['id'])}"
            })

        elif content_type in ['album', 'playlist']:
            info_func = downloader.get_album_info if content_type == 'album' else downloader.get_playlist_info
            item_info = info_func(spotify_id)
            if not item_info:
                return jsonify({'error': f'{content_type.capitalize()} not found'}), 404

            # Limit tracks for free tier
            tracks = item_info.get('tracks', [])[:20]  # Limit to 20 tracks for free tier
            
            response_data = {
                'type': content_type,
                'title': item_info.get('name'),
                'thumbnail_url': item_info['images'][0]['url'] if item_info.get('images') else None,
                'total_tracks': len(tracks),
                'zip_download_link': f"{base_url}{url_for('download_zip', item_type=content_type, item_id=spotify_id)}"
            }

            if content_type == 'album':
                response_data['artist'] = ", ".join(item_info.get('artists', []))

            tracks_list = [{
                'position': i + 1,
                'title': track.get('name'),
                'artists': track.get('artists'),
                'album_name': track.get('album'),
                'download_link': f"{base_url}{url_for('stream_file', track_id=track['id'])}"
            } for i, track in enumerate(tracks)]

            response_data['tracks'] = tracks_list
            return jsonify(response_data)

    except Exception as e:
        app.logger.error(f"Error in /download endpoint: {e}", exc_info=True)
        return jsonify({'error': 'An internal server error occurred'}), 500

@app.route('/download/stream/<track_id>')
def stream_file(track_id):
    if not track_id:
        return jsonify({'error': 'Track ID is required'}), 400

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix='vibedownloader_')
        app.logger.info(f"Created temp directory: {temp_dir}")

        track_info = downloader.get_track_info(track_id)
        if not track_info:
            return jsonify({'error': 'Track not found'}), 404

        metadata = downloader.prepare_metadata(track_info, include_image_data=True)
        query = f"{' '.join(track_info['artists'])} - {track_info['name']}"

        app.logger.info(f"Starting download for: {query}")
        downloaded_file = downloader.search_and_download_youtube(query, metadata, temp_dir)

        if not downloaded_file or not os.path.exists(downloaded_file):
            app.logger.error(f"Download failed for track {track_id}")
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({'error': 'Could not process track'}), 500

        filename = f"{BRANDING_PREFIX}{metadata['artist']} - {metadata['title']}.mp3"
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)

        @after_this_request
        def cleanup(response):
            try:
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    app.logger.info(f"Cleaned up temp directory: {temp_dir}")
            except Exception as e:
                app.logger.error(f"Error during cleanup: {e}")
            return response

        app.logger.info(f"Sending file: {downloaded_file}")
        return send_file(
            downloaded_file,
            as_attachment=True,
            download_name=filename,
            mimetype='audio/mpeg'
        )

    except Exception as e:
        app.logger.error(f"Error in stream_file: {e}", exc_info=True)
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': 'An unexpected error occurred'}), 500

@app.route('/download/zip/<item_type>/<item_id>')
def download_zip(item_type, item_id):
    if item_type not in ['album', 'playlist']:
        return jsonify({'error': 'Invalid item type for ZIP download.'}), 400

    info_func = downloader.get_album_info if item_type == 'album' else downloader.get_playlist_info
    item_info = info_func(item_id)
    if not item_info:
        return jsonify({'error': f'{item_type.capitalize()} not found.'}), 404

    # Limit tracks for free tier
    tracks = item_info.get('tracks', [])[:10]  # Limit to 10 tracks for ZIP on free tier
    
    zip_temp_dir = None
    zip_path = None

    try:
        zip_temp_dir = tempfile.mkdtemp(prefix='vibedownloader_zip_')
        app.logger.info(f"Created ZIP temp directory: {zip_temp_dir}")

        def download_track_for_zip(track):
            try:
                metadata = downloader.prepare_metadata(track, include_image_data=True)
                query = f"{' '.join(track['artists'])} - {track['name']}"
                result = downloader.search_and_download_youtube(query, metadata, zip_temp_dir)
                return f"Downloaded: {track['name']}" if result else f"Failed: {track['name']}"
            except Exception as e:
                app.logger.error(f"Error downloading track {track.get('name', 'unknown')}: {e}")
                return f"Error: {track.get('name', 'unknown')}"

        # Download tracks with limited concurrency for free tier
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_to_track = {
                executor.submit(download_track_for_zip, track): track 
                for track in tracks
            }

            results = []
            for future in as_completed(future_to_track):
                result = future.result()
                results.append(result)
                app.logger.info(result)

        zip_filename_base = f"{BRANDING_PREFIX}{item_info['name']}"
        safe_zip_base = re.sub(r'[<>:"/\\|?*]', '_', zip_filename_base)
        safe_zip_base = safe_zip_base[:150]

        zip_path = shutil.make_archive(
            os.path.join(tempfile.gettempdir(), safe_zip_base),
            'zip',
            zip_temp_dir
        )

        if not os.path.exists(zip_path):
            raise Exception("Failed to create ZIP file")

        @after_this_request
        def cleanup(response):
            try:
                if zip_temp_dir and os.path.exists(zip_temp_dir):
                    shutil.rmtree(zip_temp_dir)
                if zip_path and os.path.exists(zip_path):
                    os.remove(zip_path)
                app.logger.info(f"Cleaned up ZIP files for {item_id}")
            except Exception as e:
                app.logger.error(f"Error during ZIP cleanup: {e}")
            return response

        return send_file(
            zip_path,
            as_attachment=True,
            download_name=f"{safe_zip_base}.zip",
            mimetype='application/zip'
        )

    except Exception as e:
        app.logger.error(f"Error creating ZIP for {item_id}: {e}", exc_info=True)

        if zip_temp_dir and os.path.exists(zip_temp_dir):
            shutil.rmtree(zip_temp_dir, ignore_errors=True)
        if zip_path and os.path.exists(zip_path):
            os.remove(zip_path)

        return jsonify({'error': 'Failed to create ZIP file'}), 500

# --- Error Handlers ---
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

# --- Main execution ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(debug=False, host='0.0.0.0', port=port)
