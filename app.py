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
        pass
    return None

sp = init_spotify()

class EnhancedDownloader:
    def __init__(self):
        # Latest user agents for 2025
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0'
        ]

        # Enhanced options for 2025 with latest fixes
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
                'preferredquality': '192'
            }],
            'socket_timeout': 60,
            'retries': 5,
            'fragment_retries': 5,
            'ignoreerrors': True,
            'concurrent_fragments': 1,  # Reduced for stability
            'buffersize': 1024,
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            'prefer_free_formats': True,
            'youtube_include_dash_manifest': False,
            'http_chunk_size': 1048576,  # 1MB chunks
            'extractor_retries': 3,
            'file_access_retries': 3,
            'age_limit': 100,
            'nocheckcertificate': True,  # Bypass SSL issues
            'rm_cachedir': True,  # Clear cache to prevent 403 errors
            'cookies_from_browser': None,  # Disable browser cookies
            'no_check_certificates': True,
            'workarounds': [
                'generic',
                'youtube:player_skip=webpage,configs',
                'youtube:player_client=web,mweb'
            ]
        }
        
        self.lock = threading.Lock()
        self.cache_dir = None
        self.clear_cache()

    def clear_cache(self):
        """Clear yt-dlp cache to prevent 403 errors"""
        try:
            import yt_dlp.cache
            if hasattr(yt_dlp.cache, 'clear'):
                yt_dlp.cache.clear()
        except:
            pass

    def get_ydl_opts(self):
        """Get yt-dlp options with random user agent and latest fixes"""
        opts = self.ydl_base_opts.copy()
        
        # Randomize user agent
        selected_ua = random.choice(self.user_agents)
        opts['http_headers'] = {
            'User-Agent': selected_ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
            'Keep-Alive': '115',
            'Connection': 'keep-alive',
        }
        
        # Add proxy rotation if needed (for Render)
        if os.getenv('RENDER'):
            opts['proxy'] = None  # Disable proxy for Render
            opts['source_address'] = None
            
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
            headers = {
                'User-Agent': random.choice(self.user_agents),
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
            }
            response = requests.get(image_url, timeout=15, stream=True, headers=headers)
            response.raise_for_status()
            return response.content
        except Exception as e:
            app.logger.error(f"Image download error: {e}")
            return None

    def add_metadata(self, file_path, metadata):
        try:
            if not os.path.exists(file_path):
                return False

            time.sleep(0.5)  # Give file time to finalize

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
            if metadata.get('year'):
                audio_file['TDRC'] = TDRC(encoding=3, text=str(metadata['year']))
            if metadata.get('track_number'):
                audio_file['TRCK'] = TRCK(encoding=3, text=str(metadata['track_number']))

            # Add cover image if available and reasonable size
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
            app.logger.error(f"Metadata error: {e}")
            return False

    def get_alternative_sources(self, query):
        """Get alternative sources beyond YouTube"""
        sources = []
        
        # Multiple YouTube search strategies
        youtube_queries = [
            f"ytsearch5:{query}",
            f"ytsearch10:{query}",
            f"ytsearch5:{query} official",
            f"ytsearch5:{query} audio",
            f"ytsearch5:{query} music",
        ]
        
        sources.extend(youtube_queries)
        return sources

    def download_track_with_enhanced_fallbacks(self, query, metadata, output_dir):
        """Enhanced download with multiple fallback strategies"""
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', f"{metadata['artist']} - {metadata['title']}")
        safe_title = safe_title[:100]  # Shorter for compatibility

        # Clear cache before download
        self.clear_cache()

        # Enhanced search queries with better formatting
        search_queries = [
            f"{metadata['artist']} {metadata['title']}",
            f"{metadata['title']} {metadata['artist']}",
            f"{metadata['artist']} - {metadata['title']}",
            f"{metadata['artist']} {metadata['title']} official",
            f"{metadata['artist']} {metadata['title']} audio",
            f"{metadata['title']} - {metadata['artist']} official",
            f"{metadata['artist']} {metadata['title']} music",
            f"{metadata['title']} {metadata['artist']} song",
        ]

        for attempt, search_query in enumerate(search_queries, 1):
            app.logger.info(f"Attempt {attempt}/8: {search_query}")
            
            # Get alternative sources
            sources = self.get_alternative_sources(search_query)
            
            for source_idx, source in enumerate(sources, 1):
                try:
                    app.logger.info(f"Trying source {source_idx}: {source}")
                    
                    ydl_opts = self.get_ydl_opts()
                    ydl_opts['outtmpl'] = os.path.join(output_dir, f"{safe_title}_temp_{attempt}_{source_idx}.%(ext)s")

                    with self.lock:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            try:
                                # First extract info to check availability
                                info = ydl.extract_info(source, download=False)
                                
                                if info and 'entries' in info and info['entries']:
                                    # Filter for best match
                                    best_entry = None
                                    query_lower = search_query.lower()
                                    
                                    for entry in info['entries'][:3]:  # Check first 3 results
                                        if entry and entry.get('title'):
                                            title_lower = entry['title'].lower()
                                            # Simple relevance check
                                            if (metadata['artist'].lower() in title_lower and 
                                                metadata['title'].lower() in title_lower):
                                                best_entry = entry
                                                break
                                    
                                    if not best_entry and info['entries']:
                                        best_entry = info['entries'][0]
                                    
                                    if best_entry:
                                        # Download the selected entry
                                        single_url = best_entry['webpage_url'] if 'webpage_url' in best_entry else best_entry['url']
                                        ydl.download([single_url])

                                        # Find the downloaded file
                                        for file in os.listdir(output_dir):
                                            if file.endswith('.mp3') and f"temp_{attempt}_{source_idx}" in file:
                                                downloaded_file = os.path.join(output_dir, file)
                                                final_file = os.path.join(output_dir, f"{safe_title}.mp3")
                                                
                                                if os.path.exists(downloaded_file):
                                                    shutil.move(downloaded_file, final_file)
                                                    
                                                    # Add metadata
                                                    if self.add_metadata(final_file, metadata):
                                                        app.logger.info(f"✓ Success: {search_query}")
                                                        return final_file
                                                    else:
                                                        app.logger.warning(f"Metadata failed but file exists: {final_file}")
                                                        return final_file

                            except Exception as e:
                                app.logger.error(f"Download error for {source}: {e}")
                                continue

                except Exception as e:
                    app.logger.error(f"Source {source} failed: {e}")
                    continue

                # Delay between attempts
                time.sleep(2)

        app.logger.error(f"❌ All attempts failed for: {query}")
        return None

    def download_track(self, query, metadata, output_dir):
        """Main download method with enhanced error handling"""
        try:
            return self.download_track_with_enhanced_fallbacks(query, metadata, output_dir)
        except Exception as e:
            app.logger.error(f"Critical download error for {metadata.get('title', 'unknown')}: {e}")
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
                'track_number': track['track_number'],
                'release_date': track['album'].get('release_date', ''),
                'duration_ms': track.get('duration_ms', 0)
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
                'release_date': album.get('release_date', ''),
                'tracks': [{
                    'id': track['id'],
                    'name': track['name'],
                    'artists': [artist['name'] for artist in track['artists']],
                    'album': album['name'],
                    'images': album['images'],
                    'track_number': track['track_number'],
                    'duration_ms': track.get('duration_ms', 0)
                } for track in tracks['items'][:15] if track['id']]  # Limit to 15 tracks
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
                    'track_number': item['track']['track_number'],
                    'duration_ms': item['track'].get('duration_ms', 0)
                } for item in tracks['items'][:15] if item['track'] and item['track']['id']]  # Limit to 15 tracks
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
        'message': 'VibeDownloader API - 2025 Enhanced Edition',
        'status': 'online',
        'spotify_available': sp is not None,
        'version': '3.0',
        'last_updated': '2025-07-04',
        'features': [
            'Enhanced yt-dlp with 2025 fixes',
            'Multiple fallback strategies',
            'Proxy bypass for hosting platforms',
            'Improved metadata handling',
            'Better error handling'
        ]
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'spotify_status': 'connected' if sp else 'disconnected'
    })

@app.route('/download')
def download():
    url = request.args.get('url')
    if not url:
        return jsonify({'error': 'URL parameter required'}), 400

    if not sp:
        return jsonify({'error': 'Spotify service unavailable'}), 503

    link_info = downloader.detect_spotify_link(url)
    if not link_info:
        return jsonify({'error': 'Invalid or unsupported Spotify URL'}), 400

    content_type, spotify_id = link_info['type'], link_info['id']
    base_url = request.host_url.rstrip('/')

    try:
        if content_type == 'track':
            track_info = downloader.get_track_info(spotify_id)
            if not track_info:
                return jsonify({'error': 'Track not found or unavailable'}), 404

            return jsonify({
                'type': 'track',
                'title': track_info['name'],
                'artists': track_info['artists'],
                'album': track_info['album'],
                'duration_ms': track_info.get('duration_ms', 0),
                'thumbnail': track_info['images'][0]['url'] if track_info['images'] else None,
                'download_url': f"{base_url}/download/stream/{track_info['id']}"
            })

        elif content_type in ['album', 'playlist']:
            info_func = downloader.get_album_info if content_type == 'album' else downloader.get_playlist_info
            item_info = info_func(spotify_id)
            if not item_info:
                return jsonify({'error': f'{content_type.capitalize()} not found or unavailable'}), 404

            return jsonify({
                'type': content_type,
                'title': item_info['name'],
                'thumbnail': item_info['images'][0]['url'] if item_info['images'] else None,
                'total_tracks': len(item_info['tracks']),
                'tracks': [{
                    'title': track['name'],
                    'artists': track['artists'],
                    'duration_ms': track.get('duration_ms', 0),
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
        app.logger.info(f"🎵 Starting download for track: {track_id}")

        track_info = downloader.get_track_info(track_id)
        if not track_info:
            return jsonify({'error': 'Track not found in Spotify'}), 404

        # Extract year from release date
        year = None
        if track_info.get('release_date'):
            try:
                year = int(track_info['release_date'][:4])
            except:
                pass

        metadata = {
            'title': track_info['name'],
            'artist': ', '.join(track_info['artists']),
            'album': track_info['album'],
            'track_number': track_info.get('track_number', 1),
            'year': year
        }

        # Add cover image for single track downloads
        if track_info.get('images'):
            metadata['cover_image_data'] = downloader.download_image(track_info['images'][0]['url'])

        query = f"{metadata['artist']} {metadata['title']}"
        app.logger.info(f"🔍 Searching for: {query}")

        downloaded_file = downloader.download_track(query, metadata, temp_dir)

        if not downloaded_file or not os.path.exists(downloaded_file):
            app.logger.error(f"❌ Download failed for track {track_id}")
            return jsonify({
                'error': 'Download failed - track may not be available or accessible',
                'suggestion': 'Try again later or check if the track is available in your region'
            }), 500

        filename = f"{BRANDING_PREFIX}{metadata['artist']} - {metadata['title']}.mp3"
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)[:200]  # Limit filename length

        @after_this_request
        def cleanup(response):
            try:
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except:
                pass
            return response

        app.logger.info(f"✅ Successfully processed track: {track_id}")
        return send_file(
            downloaded_file,
            as_attachment=True,
            download_name=filename,
            mimetype='audio/mpeg'
        )

    except Exception as e:
        app.logger.error(f"❌ Stream track error: {e}")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/download/zip/<item_type>/<item_id>')
def download_zip(item_type, item_id):
    if item_type not in ['album', 'playlist']:
        return jsonify({'error': 'Invalid type - must be album or playlist'}), 400

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        app.logger.info(f"📦 Starting ZIP download for {item_type}: {item_id}")

        info_func = downloader.get_album_info if item_type == 'album' else downloader.get_playlist_info
        item_info = info_func(item_id)
        if not item_info:
            return jsonify({'error': f'{item_type.capitalize()} not found'}), 404

        # Create in-memory ZIP
        zip_buffer = io.BytesIO()
        successful_downloads = 0
        total_tracks = min(len(item_info['tracks']), 8)  # Limit to 8 tracks for performance

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for i, track in enumerate(item_info['tracks'][:total_tracks]):
                try:
                    app.logger.info(f"📥 Processing track {i+1}/{total_tracks}: {track['name']}")

                    # Extract year from release date
                    year = None
                    if item_info.get('release_date'):
                        try:
                            year = int(item_info['release_date'][:4])
                        except:
                            pass

                    metadata = {
                        'title': track['name'],
                        'artist': ', '.join(track['artists']),
                        'album': track['album'],
                        'track_number': track.get('track_number', i+1),
                        'year': year
                    }

                    query = f"{metadata['artist']} {metadata['title']}"
                    downloaded_file = downloader.download_track(query, metadata, temp_dir)

                    if downloaded_file and os.path.exists(downloaded_file):
                        zip_filename = f"{i+1:02d} - {metadata['artist']} - {metadata['title']}.mp3"
                        zip_filename = re.sub(r'[<>:"/\\|?*]', '_', zip_filename)[:150]
                        zip_file.write(downloaded_file, zip_filename)
                        os.remove(downloaded_file)  # Clean up immediately
                        successful_downloads += 1
                        app.logger.info(f"✅ Added track {i+1} to ZIP")
                    else:
                        app.logger.warning(f"⚠️ Failed to download track {i+1}: {track['name']}")

                except Exception as e:
                    app.logger.error(f"❌ Error processing track {i+1}: {e}")
                    continue

        if successful_downloads == 0:
            return jsonify({'error': 'No tracks could be downloaded successfully'}), 500

        zip_buffer.seek(0)

        @after_this_request
        def cleanup(response):
            try:
                if temp_dir:
                    shutil.rmtree(temp_dir)
            except:
                pass
            return response

        app.logger.info(f"🎉 ZIP created successfully with {successful_downloads}/{total_tracks} tracks")
        
        zip_filename = f"{BRANDING_PREFIX}{item_info['name']}.zip"
        zip_filename = re.sub(r'[<>:"/\\|?*]', '_', zip_filename)[:200]
        
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=zip_filename,
            mimetype='application/zip'
        )

    except Exception as e:
        app.logger.error(f"❌ ZIP download error: {e}")
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'error': 'ZIP creation failed'}), 500

# --- Enhanced Error Handlers ---
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'error': 'Endpoint not found',
        'message': 'The requested resource does not exist'
    }), 404

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f"Internal server error: {error}")
    return jsonify({
        'error': 'Internal server error',
        'message': 'Something went wrong on our end'
    }), 500

@app.errorhandler(503)
def service_unavailable(error):
    return jsonify({
        'error': 'Service temporarily unavailable',
        'message': 'Please try again later'
    }), 503

# --- Production Entry Point ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.logger.info(f"🚀 Starting VibeDownloader API on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
