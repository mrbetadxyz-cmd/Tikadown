import os
import re
import json
import logging
import urllib.request
import urllib.parse
import asyncio
from pathlib import Path
import yt_dlp

logger = logging.getLogger(__name__)

CACHE_FILE = Path("search_cache.json")
_cache = {}

def load_cache():
    global _cache
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load search cache: {e}")
            _cache = {}

def save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save search cache: {e}")

# Load cache on import
load_cache()

def resolve_youtube_search_ytdlp(query: str) -> str:
    """
    Search YouTube using yt-dlp's ytsearch1: search extractor.
    """
    try:
        # Check if cookies.txt exists
        cookie_file = Path(__file__).parent / "cookies.txt"
        if not cookie_file.exists():
            cookie_file = Path("cookies.txt")
            
        from downloader import is_warp_proxy_available, is_valid_netscape_cookie_file
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist',
            'skip_download': True,
        }
        if is_warp_proxy_available():
            ydl_opts['proxy'] = 'socks5://127.0.0.1:40000'
            
        if cookie_file.exists():
            if is_valid_netscape_cookie_file(cookie_file):
                ydl_opts['cookiefile'] = str(cookie_file.resolve())
            
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if res and 'entries' in res and len(res['entries']) > 0:
                video_url = res['entries'][0].get('url')
                if video_url:
                    if not video_url.startswith('http'):
                        video_url = f"https://www.youtube.com/watch?v={video_url}"
                    return video_url
    except Exception as e:
        logger.error(f"yt-dlp search failed for query '{query}': {e}")
    return None

def resolve_youtube_search_invidious(query: str) -> str:
    """
    Search YouTube via public Invidious instances for the query and return the watch URL.
    """
    # A list of reliable public Invidious instances
    instances = [
        "invidious.yewtu.be",
        "vid.puffyan.us",
        "inv.tux.im",
        "invidious.nerdvpn.de",
        "invidious.flokinet.to",
        "invidious.privacydev.net",
        "iv.melmac.space",
        "invidious.no-logs.com"
    ]
    
    encoded_query = urllib.parse.quote(query)
    
    for instance in instances:
        url = f"https://{instance}/api/v1/search?q={encoded_query}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    # Find the first item with type "video"
                    for item in data:
                        if isinstance(item, dict) and item.get("type") == "video":
                            video_id = item.get("videoId")
                            if video_id:
                                return f"https://www.youtube.com/watch?v={video_id}"
        except Exception as e:
            logger.debug(f"Invidious instance {instance} failed: {e}")
            continue
            
    return None

async def get_youtube_url_by_query(query: str) -> str:
    """
    Search for a song query directly on YouTube via yt-dlp (primary) and Invidious (fallback).
    """
    clean_query = query.strip()
    
    # Check cache first
    if clean_query in _cache:
        logger.info(f"Cache hit for search: {clean_query} -> {_cache[clean_query]}")
        return _cache[clean_query]
        
    logger.info(f"Searching YouTube (yt-dlp) for: {clean_query}")
    resolved_url = await asyncio.to_thread(resolve_youtube_search_ytdlp, clean_query)
    
    # Fallback to Invidious if yt-dlp search failed
    if not resolved_url:
        logger.warning(f"yt-dlp search failed for '{clean_query}'. Trying Invidious fallback...")
        resolved_url = await asyncio.to_thread(resolve_youtube_search_invidious, clean_query)
        
    if not resolved_url:
        raise ValueError(f"Could not find YouTube video for query: {query}")
        
    # Update cache
    _cache[clean_query] = resolved_url
    save_cache()
    
    return resolved_url
