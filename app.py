# --- Enhanced Spotify Downloader API with YouTube Fixes ---
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

# --- Logging ---
logging.basicConfig(level=logging.WARNING)
app.logger.setLevel(logging.WARNING)

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
    except:
        pass
    return None

sp = init_spotify()

class EnhancedDownloader:
    def __init__(self):
        # Multiple user agents to rotate through
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]
        
        # Enhanced yt-dlp options with multiple fallbacks
        self.ydl_base_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extractaudio': True,
            'audioformat': 'mp3',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320'
            }],
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'ignoreerrors': True,
            'concurrent_fragments': 2,
            'buffersize': 2048,
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            'prefer_free_formats': True,
            'youtube_include_dash_manifest': False,
            'http_chunk_size': 10485760,  # 10MB chunks
        }
        self.lock = threading.Lock()
        
        # Alternative search engines/sources
        self.search_engines = [
            'ytsearch5:',  # YouTube search (5 results)
            'ytsearch10:',  # YouTube search (10 results)
        ]

    def get_ydl_opts(self):
        """Get yt-dlp options with random user agent"""
        opts = self.ydl_base_opts.copy()
        opts['http_headers'] = {
            'User-Agent': random.choice(self.user_agents)
        }
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
        except:
            pass
        return None

    def download_image(self, image_url):
        try:
            headers = {'User-Agent': random.choice(self.user_agents)}
            response = requests.get(image_url, timeout=10, stream=True, headers=headers)
            response.raise_for_status()
            return response.content
        except:
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

            # Add cover image if available and reasonable size
            if metadata.get('cover_image_data') and len(metadata['cover_image_data']) < 500000:
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
            app.logger.error(f"Metadata error: {e}")
            return False

    def download_track_with_fallbacks(self, query, metadata, output_dir):
        """Try multiple approaches to download a track"""
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', f"{metadata['artist']} - {metadata['title']}")
        safe_title = safe_title[:150]
        
        # Try different search queries
        search_queries = [
            f"{metadata['artist']} - {metadata['title']}",
            f"{metadata['artist']} {metadata['title']}",
            f"{metadata['title']} {metadata['artist']}",
            f"{metadata['artist']} {metadata['title']} official",
            f"{metadata['artist']} {metadata['title']} audio",
            f"{metadata['title']} - {metadata['artist']}",
        ]
        
        for attempt, search_query in enumerate(search_queries, 1):
            app.logger.info(f"Download attempt {attempt} for '{search_query}'")
            
            for search_engine in self.search_engines:
                try:
                    ydl_opts = self.get_ydl_opts()
                    ydl_opts['outtmpl'] = os.path.join(output_dir, f"{safe_title}_attempt_{attempt}.%(ext)s")
                    
                    with self.lock:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            full_query = f"{search_engine}{search_query}"
                            app.logger.info(f"Trying: {full_query}")
                            
                            # Extract info first to check availability
                            info = ydl.extract_info(full_query, download=False)
                            if info and 'entries' in info and info['entries']:
                                # Download the first available entry
                                ydl.download([full_query])
                                
                                # Find the downloaded file
                                for file in os.listdir(output_dir):
                                    if file.endswith('.mp3') and f"attempt_{attempt}" in file:
                                        downloaded_file = os.path.join(output_dir, file)
                                        
                                        # Rename to final name
                                        final_file = os.path.join(output_dir, f"{safe_title}.mp3")
                                        if os.path.exists(downloaded_file):
                                            shutil.move(downloaded_file, final_file)
                                            
                                            # Add metadata
                                            self.add_metadata(final_file, metadata)
                                            app.logger.info(f"Successfully downloaded: {search_query}")
                                            return final_file
                                        
                except Exception as e:
                    app.logger.error(f"yt-dlp download error: {e}")
                    continue
                    
                # Small delay between attempts
                time.sleep(1)
        
        app.logger.error(f"All download attempts failed for: {query}")
        return None

    def download_track(self, query, metadata, output_dir):
        """Main download method with enhanced error handling"""
        try:
            return self.download_track_with_fallbacks(query, metadata, output_dir)
        except Exception as e:
            app.logger.error(f"Download failed for track {metadata.get('title', 'unknown')}: {e}")
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
            app.logger.error(f"Error getting track info: {e}")
            return None

    def get_album_info(self, album_id):
        if not sp:
            return None
        try:
            album = sp.album(album_id)
            tracks = sp.album_tracks(album_id, limit=50)
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
                } for track in tracks['items'][:20]]
            }
        except Exception as e:
            app.logger.error(f"Error getting album info: {e}")
            return None

    def get_playlist_info(self, playlist_id):
        if not sp:
            return None
        try:
            playlist = sp.playlist(playlist_id)
            tracks = sp.playlist_tracks(playlist_id, limit=50)
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
                } for item in tracks['items'][:20] if item['track'] and item['track']['id']]
            }
        except Exception as e:
            app.logger.error(f"Error getting playlist info: {e}")
            return None

# Initialize downloader
downloader = EnhancedDownloader()

# --- API Endpoints ---
@app.route('/')
def home():
    return jsonify({
        'message': 'VibeDownloader API - Enhanced',
        'status': 'ok',
        'spotify_available': sp is not None,
        'version': '2.0'
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
        return jsonify({'error': 'Spotify unavailable'}), 503

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
                'download_url': f"{base_url}/download/stream/{track_info['id']}"
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
                    'download_url': f"{base_url}/download/stream/{track['id']}"
                } for track in item_info['tracks']],
                'zip_url': f"{base_url}/download/zip/{content_type}/{spotify_id}"
            })

    except Exception as e:
        app.logger.error(f"Download endpoint error: {e}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/download/stream/<track_id>')
def stream_track(track_id):
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        app.logger.info(f"Starting download for track {track_id}")

        track_info = downloader.get_track_info(track_id)
        if not track_info:
            return jsonify({'error': 'Track not found'}), 404

        metadata = {
            'title': track_info['name'],
            'artist': ', '.join(track_info['artists']),
            'album': track_info['album']
        }

        # Add cover image for single track downloads
        if track_info.get('images'):
            metadata['cover_image_data'] = downloader.download_image(track_info['images'][0]['url'])

        query = f"{metadata['artist']} - {metadata['title']}"
        app.logger.info(f"Downloading: {query}")
        
        downloaded_file = downloader.download_track(query, metadata, temp_dir)

        if not downloaded_file or not os.path.exists(downloaded_file):
            app.logger.error(f"Download failed for track {track_id}")
            return jsonify({'error': 'Download failed - track may be unavailable'}), 500

        filename = f"{BRANDING_PREFIX}{metadata['artist']} - {metadata['title']}.mp3"
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)

        @after_this_request
        def cleanup(response):
            try:
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except:
                pass
            return response

        app.logger.info(f"Successfully processed track {track_id}")
        return send_file(
            downloaded_file,
            as_attachment=True,
            download_name=filename,
            mimetype='audio/mpeg'
        )

    except Exception as e:
        app.logger.error(f"Stream track error: {e}")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': 'Server error'}), 500

@app.route('/download/zip/<item_type>/<item_id>')
def download_zip(item_type, item_id):
    if item_type not in ['album', 'playlist']:
        return jsonify({'error': 'Invalid type'}), 400

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        app.logger.info(f"Starting ZIP download for {item_type} {item_id}")

        info_func = downloader.get_album_info if item_type == 'album' else downloader.get_playlist_info
        item_info = info_func(item_id)
        if not item_info:
            return jsonify({'error': f'{item_type} not found'}), 404

        # Create in-memory ZIP
        zip_buffer = io.BytesIO()
        successful_downloads = 0

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for i, track in enumerate(item_info['tracks'][:10]):  # Limit to 10 tracks
                try:
                    app.logger.info(f"Processing track {i+1}/10: {track['name']}")
                    
                    metadata = {
                        'title': track['name'],
                        'artist': ', '.join(track['artists']),
                        'album': track['album']
                    }

                    query = f"{metadata['artist']} - {metadata['title']}"
                    downloaded_file = downloader.download_track(query, metadata, temp_dir)

                    if downloaded_file and os.path.exists(downloaded_file):
                        zip_filename = f"{i+1:02d} - {metadata['artist']} - {metadata['title']}.mp3"
                        zip_filename = re.sub(r'[<>:"/\\|?*]', '_', zip_filename)
                        zip_file.write(downloaded_file, zip_filename)
                        os.remove(downloaded_file)  # Clean up immediately
                        successful_downloads += 1
                        app.logger.info(f"Successfully added track {i+1}")
                    else:
                        app.logger.warning(f"Failed to download track {i+1}: {track['name']}")
                        
                except Exception as e:
                    app.logger.error(f"Error processing track {i+1}: {e}")
                    continue

        if successful_downloads == 0:
            return jsonify({'error': 'No tracks could be downloaded'}), 500

        zip_buffer.seek(0)

        @after_this_request
        def cleanup(response):
            try:
                if temp_dir:
                    shutil.rmtree(temp_dir)
            except:
                pass
            return response

        app.logger.info(f"ZIP created with {successful_downloads} tracks")
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f"{item_info['name']}.zip",
            mimetype='application/zip'
        )

    except Exception as e:
        app.logger.error(f"ZIP download error: {e}")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': 'ZIP creation failed'}), 500

# --- Error Handlers ---
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Server error'}), 500

# --- Production Entry Point ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
