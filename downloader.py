import asyncio
import html
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
import yt_dlp
from config import settings

logger = logging.getLogger(__name__)

class MyLogger:
    def debug(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        pass

import urllib.request
import urllib.parse
import json

def resolve_url(url: str) -> str:
    # Resolve redirects for any short links, wrappers, or social sharing wrappers (like l.facebook.com)
    url = url.strip()
    
    # Extract clean URL and fix missing schemes (e.g. .tiktok.com/... or raw domain)
    import re
    url_match = re.search(r'(https?://[^\s]+)', url)
    if url_match:
        url = url_match.group(1)
    else:
        # Match a domain-like string (e.g. vt.tiktok.com/... or .tiktok.com/...)
        domain_match = re.search(r'([a-zA-Z0-9.-]+\.[a-zA-Z]{2,6}/[^\s]*)', url)
        if domain_match:
            url = "https://" + domain_match.group(1).lstrip('.')
        else:
            url = url.split()[0]
    
    # Clean up Pinterest tracking/sent parameters if present
    if "pinterest.com/pin/" in url:
        import re
        match = re.search(r'(https?://(www\.)?pinterest\.com/pin/\d+)', url)
        if match:
            url = match.group(1) + "/"

    # If it's a Facebook wrapper/redirection link (e.g. l.facebook.com/l.php?u=TARGET_URL)
    if "l.facebook.com/l.php" in url or "l.instagram.com" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            queries = urllib.parse.parse_qs(parsed.query)
            if 'u' in queries:
                url = queries['u'][0]
        except Exception as e:
            logger.error(f"Failed to parse Facebook redirection URL: {e}")
    
    # Facebook share links (e.g. facebook.com/share/v/ or fb.watch) need redirect resolution
    is_facebook_share = any(sh in url for sh in ["facebook.com/share/", "fb.watch", "fb.me", "l.facebook.com"])
    
    # Skip resolving standard long canonical URLs to save request time
    is_already_canonical = (not is_facebook_share) and (any(pat in url for pat in [
        "instagram.com/p/", "instagram.com/reel/", 
        "tiktok.com/@", "/video/",
        "youtube.com/shorts/", "youtube.com/watch", "youtu.be/",
        "soundcloud.com", "snd.sc",
        "twitter.com/", "x.com/",
        "pinterest.com/pin/"
    ]) or ("facebook.com/" in url and not is_facebook_share))
    
    if is_already_canonical:
        return url
        
    try:
        ua = 'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)' if ('facebook.com' in url or 'fb.watch' in url or 'fb.me' in url) else 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': ua}
        )
        # Fetch with redirection allowed
        with urllib.request.urlopen(req, timeout=8) as response:
            final_url = response.geturl()
            
            # Clean up Pinterest tracking/sent parameters from final URL
            if "pinterest.com/pin/" in final_url:
                import re
                match = re.search(r'(https?://(www\.)?pinterest\.com/pin/\d+)', final_url)
                if match:
                    final_url = match.group(1) + "/"

            # Check if the final URL is also a redirect wrapper
            if "l.facebook.com/l.php" in final_url or "l.instagram.com" in final_url:
                parsed = urllib.parse.urlparse(final_url)
                queries = urllib.parse.parse_qs(parsed.query)
                if 'u' in queries:
                    final_url = queries['u'][0]
                    # Resolve recursively in case the destination is also a redirect/shortlink
                    return resolve_url(final_url)
                    
            logger.info(f"Resolved redirect: {url} -> {final_url}")
            return final_url
    except Exception as e:
        logger.error(f"Failed to resolve redirect for {url}: {e}")
    return url

def fetch_tikwm_data(url: str) -> dict:
    api_url = "https://www.tikwm.com/api/"
    data = urllib.parse.urlencode({'url': url}).encode('utf-8')
    req = urllib.request.Request(api_url, data=data, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if res_data.get('code') == 0:
                return res_data.get('data', {})
    except Exception as e:
        logger.error(f"TikWM API request failed for {url}: {e}")
import re

def fetch_pinterest_image(url: str) -> dict:
    """
    Extract Pinterest image using multiple fallback methods.
    Priority: 1) Widgets API (widgets.pinterest.com - never blocked)
              2) oEmbed API (pinterest.com/oembed.json)
              3) HTML scraping (residential IPs only)
    """
    # Extract pin ID from URL
    pin_id_match = re.search(r'/pin/(\d+)', url)
    
    # --- Primary: Pinterest Widgets API (different subdomain, never blocked) ---
    if pin_id_match:
        pin_id = pin_id_match.group(1)
        try:
            widgets_url = f"https://widgets.pinterest.com/v3/pidgets/pins/info/?pin_ids={pin_id}"
            req = urllib.request.Request(widgets_url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json'
            })
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                pins = data.get('data', [])
                if pins:
                    pin = pins[0]
                    
                    # Try to get originals URL from story_pin_data first
                    image_url = None
                    story_data = pin.get('story_pin_data')
                    if story_data:
                        pages = story_data.get('pages', [])
                        if pages:
                            blocks = pages[0].get('blocks', [])
                            if blocks:
                                orig = blocks[0].get('image', {}).get('images', {}).get('originals', {})
                                if orig.get('url'):
                                    image_url = orig['url']
                    
                    # Fall back to largest available image and upgrade to originals
                    if not image_url:
                        images = pin.get('images', {})
                        for size in ['564x', '474x', '236x']:
                            img_data = images.get(size, {})
                            if img_data.get('url'):
                                image_url = img_data['url']
                                break
                    
                    if image_url:
                        # Upgrade to full originals resolution
                        high_res_url = image_url
                        for res in ['/136x/', '/236x/', '/474x/', '/564x/', '/736x/']:
                            high_res_url = high_res_url.replace(res, '/originals/')
                        
                        # Get creator info
                        creator = pin.get('native_creator', {})
                        author = creator.get('full_name', '').strip() or 'Pinterest Creator'
                        
                        return {
                            'is_image_pin': True,
                            'title': 'Pinterest Image',
                            'image_url': high_res_url,
                            'platform': 'pinterest',
                            'uploader': author
                        }
        except Exception as e:
            logger.warning(f"Pinterest widgets API failed for {url}: {e}")

    # --- Fallback 1: Pinterest oEmbed API ---
    try:
        oembed_api = f"https://www.pinterest.com/oembed.json?url={urllib.parse.quote(url, safe=':/')}"
        req = urllib.request.Request(oembed_api, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            thumb = data.get('thumbnail_url', '')
            if thumb and 'i.pinimg.com' in thumb:
                high_res_url = thumb
                for res in ['/136x/', '/236x/', '/474x/', '/564x/', '/736x/']:
                    high_res_url = high_res_url.replace(res, '/originals/')
                
                title = data.get('title', '').strip() or 'Pinterest Image'
                author = data.get('author_name', '').strip() or 'Pinterest Creator'
                
                return {
                    'is_image_pin': True,
                    'title': title,
                    'image_url': high_res_url,
                    'platform': 'pinterest',
                    'uploader': author
                }
    except Exception as e:
        logger.warning(f"Pinterest oembed API failed for {url}: {e}")

    # --- Fallback 2: HTML scraping (works from residential IPs) ---
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            
            og_match = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html_content)
            if not og_match:
                og_match = re.search(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', html_content)
                
            image_url = None
            if og_match:
                image_url = og_match.group(1)
            else:
                matches = re.findall(r'https://i\.pinimg\.com/originals/[a-zA-Z0-9_/.-]+', html_content)
                if matches:
                    image_url = matches[0]
            
            if image_url:
                high_res_url = image_url
                for res in ['/136x/', '/236x/', '/474x/', '/564x/', '/736x/']:
                    high_res_url = high_res_url.replace(res, '/originals/')
                
                return {
                    'is_image_pin': True,
                    'title': 'Pinterest Image',
                    'image_url': high_res_url,
                    'platform': 'pinterest',
                    'uploader': 'Pinterest Creator'
                }
    except Exception as e:
        logger.error(f"Pinterest image extraction failed for {url}: {e}")
    return {}

def _extract_instagram_caption_from_html(page_html: str) -> tuple[str, str]:
    """Extracts (caption, uploader) from an Instagram /embed/captioned/ HTML page."""
    uploader = ""
    m_user = re.search(
        r'<a[^>]+class=["\'][^"\']*CaptionUsername[^"\']*["\'][^>]*>(.*?)</a>',
        page_html,
        re.IGNORECASE | re.DOTALL
    )
    if m_user:
        uploader = html.unescape(re.sub(r'<[^>]+>', '', m_user.group(1))).strip()

    caption = ""
    m_cap = re.search(
        r'<div[^>]+class=["\'][^"\']*Caption["\'][^>]*>(.*?)</div>',
        page_html,
        re.IGNORECASE | re.DOTALL
    )
    if m_cap:
        raw_cap = m_cap.group(1)
        raw_cap = re.sub(
            r'<a[^>]+class=["\'][^"\']*CaptionUsername[^"\']*["\'][^>]*>.*?</a>',
            '',
            raw_cap,
            flags=re.IGNORECASE | re.DOTALL
        )
        raw_cap = re.sub(
            r'<div[^>]+class=["\'][^"\']*CaptionComments[^"\']*["\'][^>]*>.*',
            '',
            raw_cap,
            flags=re.IGNORECASE | re.DOTALL
        )
        raw_cap = re.sub(r'<br\s*/?>', ' ', raw_cap, flags=re.IGNORECASE)
        raw_cap = re.sub(r'<[^>]+>', ' ', raw_cap)
        caption = html.unescape(re.sub(r'\s+', ' ', raw_cap)).strip()

    if not caption:
        m_json = re.search(
            r'"edge_media_to_caption":\{"edges":\[\{"node":\{"text":"((?:\\.|[^"\\])*)"',
            page_html
        )
        if m_json:
            try:
                caption = json.loads('"' + m_json.group(1) + '"').strip()
            except Exception:
                pass

    return caption, uploader


def fetch_instagram_caption(url: str) -> str:
    """
    Fetches the real post/reel caption from Instagram's unauthenticated embed/captioned endpoint
    when yt-dlp only yields a generic 'Video by <user>' title and no description.
    """
    code_match = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    if not code_match:
        return ""
    code = code_match.group(1)
    embed_url = f"https://www.instagram.com/p/{code}/embed/captioned/"
    try:
        req = urllib.request.Request(
            embed_url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            page_html = response.read().decode('utf-8', errors='ignore')
        caption, _ = _extract_instagram_caption_from_html(page_html)
        return caption
    except Exception as e:
        logger.debug(f"Instagram caption fetch failed for {url}: {e}")
    return ""


def fetch_instagram_image(url: str) -> dict:
    """
    Fallback Instagram extractor for photo/album posts when yt-dlp returns 'No video formats found'.
    Fetches the unauthenticated embed/captioned HTML page and extracts all photo CDN URLs and caption.
    """
    code_match = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    if not code_match:
        return {}
        
    code = code_match.group(1)
    embed_url = f"https://www.instagram.com/p/{code}/embed/captioned/"
    
    req = urllib.request.Request(
        embed_url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            page_html = response.read().decode('utf-8', errors='ignore')
            caption, uploader = _extract_instagram_caption_from_html(page_html)
            
            raw_urls = re.findall(r'https?:[/\\]+(?:[^\s"\'<>]*?)(?:cdninstagram|scontent)[^\s"\'<>]*', page_html)
            logger.info(f"Instagram embed fetched, raw_urls count: {len(raw_urls)}")
            clean_urls = []
            seen = set()
            for u in raw_urls:
                url_clean = html.unescape(u).replace('\\/', '/').replace('\\u002F', '/').replace('\\u002f', '/').replace('\\u0026', '&').replace('\\u00252F', '/').replace('\\u00253D', '=').replace('\\u002526', '&').strip()
                if ',' in url_clean:
                    url_clean = url_clean.split(',')[-1]
                if any(x in url_clean for x in ['profile_pic', 's100x100', 's150x150', 'rsrc.php', 'static.cdninstagram.com', 's100x']):
                    continue
                if url_clean not in seen and ('.jpg' in url_clean or '.png' in url_clean or '.webp' in url_clean):
                    seen.add(url_clean)
                    clean_urls.append(url_clean)

            media_dict = {}
            for u in clean_urls:
                m_id = re.search(r'/\d+_(\d+)_\d+_n\.', u)
                key = m_id.group(1) if m_id else u
                
                if key not in media_dict:
                    media_dict[key] = u
                else:
                    current_best = media_dict[key]
                    if ('1080' in u or '1369' in u) and not ('1080' in current_best or '1369' in current_best):
                        media_dict[key] = u
                    elif 'c0.' not in u and 'c0.' in current_best:
                        media_dict[key] = u

            img_urls = list(media_dict.values())
                    
            if img_urls:
                return {
                    'is_slideshow': True,
                    'title': caption or f'Instagram Post ({code})',
                    'images': img_urls,
                    'platform': 'instagram',
                    'uploader': uploader or 'Instagram Creator'
                }
    except Exception as e:
        logger.error(f"Instagram image fallback failed for {url}: {e}")
        
    return {}

def _is_generic_fb_title(text: str) -> bool:
    if not text:
        return True
    low = text.strip().lower()
    if low in {
        "facebook", "facebook watch", "watch", "video", "reels", "reel",
        "log in or sign up to view", "log into facebook", "social video",
        "see posts, photos and more on facebook."
    }:
        return True
    if re.match(r'^\d[\d.,KMBkmb]*\s+(?:reactions?|views?|likes?|comments?|shares?)', low):
        return True
    return False


def _clean_facebook_caption(text: str, uploader: str = None) -> str:
    if not text:
        return ""
    cleaned = html.unescape(text).strip()
    cleaned = re.sub(
        r'^\d[\d.,KMBkmb]*\s+(?:reactions?|views?|likes?|comments?|shares?)(?:\s*[·•|]\s*\d[\d.,KMBkmb]*\s+(?:reactions?|views?|likes?|comments?|shares?))*\s*\|\s*',
        '',
        cleaned,
        flags=re.IGNORECASE
    ).strip()
    if uploader:
        suffix = f"| {uploader}".strip()
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)].strip()
    if cleaned.endswith("| Facebook"):
        cleaned = cleaned[:-len("| Facebook")].strip()
    return cleaned


def fetch_facebook_caption(url: str, uploader: str = None) -> str:
    """
    Fetches the real post/reel caption for a Facebook URL using Facebook's
    OpenGraph externalhit crawler headers when yt-dlp returns a generic 'Facebook' title.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            }
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode('utf-8', errors='ignore')

        def _meta(key: str, attr: str = "property") -> str:
            for pat in (
                rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']*)["\']',
                rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+{attr}=["\']{re.escape(key)}["\']',
            ):
                m = re.search(pat, body, re.IGNORECASE | re.DOTALL)
                if m and m.group(1).strip():
                    return m.group(1).strip()
            return ""

        candidates = []
        og_desc = _meta("og:description")
        if og_desc:
            candidates.append(og_desc)

        m_msg = re.search(r'"message":\{"text":"((?:\\.|[^"\\])*)"', body)
        if m_msg:
            try:
                candidates.append(json.loads('"' + m_msg.group(1) + '"'))
            except Exception:
                pass

        m_title = re.search(r'<title[^>]*>(.*?)</title>', body, re.IGNORECASE | re.DOTALL)
        if m_title:
            t_val = html.unescape(m_title.group(1)).strip()
            # Strip trailing " | Author" from <title> if present
            if " | " in t_val:
                t_val = t_val.rsplit(" | ", 1)[0].strip()
            candidates.append(t_val)

        og_title = _meta("og:title")
        if og_title:
            candidates.append(og_title)

        for cand in candidates:
            cleaned = _clean_facebook_caption(cand, uploader=uploader)
            if cleaned and not _is_generic_fb_title(cleaned):
                return cleaned
    except Exception as e:
        logger.debug(f"Facebook caption fetch fallback failed for {url}: {e}")
    return ""


INVIDIOUS_INSTANCES = [
    "https://invidious.privacydev.net",
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yewtu.be",
    "https://invidious.jing.rocks",
    "https://vid.priv.au",
    "https://invidious.drgns.space",
    "https://inv.tux.pizza"
]

def extract_youtube_video_id(url: str) -> str:
    """Extract YouTube 11-character video ID from any YouTube URL"""
    m = re.search(r'(?:v=|\/|youtu\.be\/|shorts\/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None

def fetch_invidious_youtube_data(url: str) -> dict:
    """
    Direct Invidious API fallback to resolve YouTube video/audio streams
    WITHOUT needing any cookies or VPS bot authentication.
    """
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return {}
        
    for instance in INVIDIOUS_INSTANCES:
        try:
            api_url = f"{instance}/api/v1/videos/{video_id}"
            req = urllib.request.Request(api_url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            with urllib.request.urlopen(req, timeout=6) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    title = data.get('title', 'YouTube Video')
                    duration = data.get('lengthSeconds', 0)
                    uploader = data.get('author', 'YouTube Creator')
                    
                    # Thumbnail
                    thumbnails = data.get('videoThumbnails', [])
                    thumbnail_url = thumbnails[0].get('url') if thumbnails else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    if thumbnail_url and thumbnail_url.startswith('/'):
                        thumbnail_url = f"{instance}{thumbnail_url}"
                        
                    format_streams = data.get('formatStreams', [])
                    adaptive_formats = data.get('adaptiveFormats', [])
                    
                    hd_url, sd_url = None, None
                    for fs in format_streams:
                        quality = fs.get('qualityLabel', '')
                        stream_url = fs.get('url')
                        if not stream_url:
                            continue
                        if '720p' in quality or '1080p' in quality:
                            hd_url = stream_url
                        elif '360p' in quality or '480p' in quality:
                            sd_url = stream_url
                            
                    if not sd_url and format_streams:
                        sd_url = format_streams[-1].get('url')
                    if not hd_url and format_streams:
                        hd_url = format_streams[0].get('url')
                        
                    audio_url = None
                    for af in adaptive_formats:
                        if af.get('type', '').startswith('audio/') or af.get('container') in ['m4a', 'opus', 'webm']:
                            audio_url = af.get('url')
                            break
                            
                    if not audio_url and sd_url:
                        audio_url = sd_url
                        
                    if hd_url or sd_url or audio_url:
                        logger.info(f"Successfully resolved YouTube video via Invidious instance {instance}")
                        return {
                            'id': video_id,
                            'title': title,
                            'duration': duration,
                            'uploader': uploader,
                            'thumbnail': thumbnail_url,
                            'webpage_url': f"https://www.youtube.com/watch?v={video_id}",
                            'is_invidious_fallback': True,
                            'platform': 'youtube',
                            'hd_url': hd_url or sd_url,
                            'sd_url': sd_url or hd_url,
                            'audio_url': audio_url or sd_url or hd_url,
                            'formats': []
                        }
        except Exception as e:
            logger.warning(f"Invidious instance {instance} failed for video {video_id}: {e}")
            continue
            
    return {}

def is_valid_netscape_cookie_file(cookie_path: Path) -> bool:
    """
    Validates whether a cookies.txt file is formatted properly in Netscape format.
    Prevents yt-dlp crashing with 'does not look like a Netscape format cookies file'.
    """
    if not cookie_path.is_file() or cookie_path.stat().st_size < 10:
        return False
    try:
        with open(cookie_path, 'r', encoding='utf-8', errors='ignore') as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                line = line.strip()
                if "Netscape HTTP Cookie File" in line or "# HTTP Cookie File" in line:
                    return True
                if line and not line.startswith('#') and '\t' in line:
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        return True
    except Exception as e:
        logger.warning(f"Error checking cookie file {cookie_path}: {e}")
def is_warp_proxy_available(host='127.0.0.1', port=40000) -> bool:
    """Checks if local Cloudflare WARP SOCKS5 proxy is active on port 40000"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        res = s.connect_ex((host, port))
        s.close()
        return res == 0
    except Exception:
        return False

KNOWN_SOCIAL_DOMAINS = (
    "tiktok.com", "instagram.com", "instagr.am",
    "pinterest.com", "pin.it", "facebook.com", "fb.watch", "fb.me",
    "youtube.com", "youtu.be", "twitter.com", "x.com",
    "soundcloud.com", "snd.sc",
)


def _is_known_social_url(url: str | None) -> bool:
    """Returns True if url hostname matches one of the core social media platforms."""
    if not url:
        return False
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in KNOWN_SOCIAL_DOMAINS)


_ARIA2C_PROGRESS_RE = re.compile(
    r'([\d.]+)(B|KiB|MiB|GiB)/([\d.]+)(B|KiB|MiB|GiB)\(\d+%\).*?DL:([\d.]+)(B|KiB|MiB|GiB)'
)


def _size_token_to_bytes(val_str: str, unit: str) -> int:
    """Convert an aria2c size token (e.g. '42.5', 'MiB') to integer bytes."""
    mult = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3}.get(unit, 1)
    return int(float(val_str) * mult)


def _cleanup_aria2c_artifacts(path: Path):
    """Remove partial file and .aria2 control file if present."""
    for target in (path, Path(f"{path}.aria2")):
        if target.exists():
            try:
                target.unlink()
            except Exception:
                pass


def _download_file_aria2c(
    media_url: str,
    http_headers: dict | None,
    output_path: str | Path,
    progress_hook=None,
    cancel_event=None,
) -> bool:
    """
    Download a direct HTTP/HTTPS media stream using 16 parallel connections via aria2c,
    relaying live progress to yt-dlp-style progress_hook and supporting immediate cancel_event.
    Returns True on success, False if aria2c failed (caller falls back to yt-dlp).
    """
    aria2_bin = shutil.which("aria2c")
    if not aria2_bin:
        return False

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_aria2c_artifacts(out_path)

    cmd = [
        aria2_bin,
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--file-allocation=none",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--max-tries=3",
        "--retry-wait=1",
        "--connect-timeout=15",
        "--timeout=30",
        "--summary-interval=1",
        "--console-log-level=warn",
        f"--dir={out_path.parent}",
        f"--out={out_path.name}",
    ]
    for key, val in (http_headers or {}).items():
        if not key or val is None:
            continue
        k_low = str(key).lower()
        clean_val = str(val).replace("\r", "").replace("\n", "").strip()
        if k_low == "user-agent":
            cmd.append(f"--user-agent={clean_val}")
        elif k_low == "referer":
            cmd.append(f"--referer={clean_val}")
        elif k_low not in {"host", "content-length", "accept-encoding"}:
            cmd.append(f"--header={key}: {clean_val}")
    cmd.append(media_url)

    state = {"downloaded": 0, "total": 0, "speed": 0}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def _read_progress():
        buf = ""
        fd = proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="ignore")
            parts = re.split(r'[\r\n]+', buf)
            buf = parts[-1]
            for line in parts[:-1]:
                m = _ARIA2C_PROGRESS_RE.search(line)
                if m:
                    state["downloaded"] = _size_token_to_bytes(m.group(1), m.group(2))
                    state["total"] = _size_token_to_bytes(m.group(3), m.group(4))
                    state["speed"] = _size_token_to_bytes(m.group(5), m.group(6))

    reader = threading.Thread(target=_read_progress, daemon=True)
    reader.start()

    try:
        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                proc.wait()
                _cleanup_aria2c_artifacts(out_path)
                raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
            if progress_hook and (state["downloaded"] > 0 or state["total"] > 0):
                progress_hook({
                    "status": "downloading",
                    "downloaded_bytes": state["downloaded"],
                    "total_bytes": state["total"],
                    "speed": state["speed"],
                })
            time.sleep(0.2)
    finally:
        reader.join(timeout=0.5)
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass

    if cancel_event and cancel_event.is_set():
        _cleanup_aria2c_artifacts(out_path)
        raise yt_dlp.utils.DownloadCancelled("Cancelled by user")

    if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
        ctrl = Path(f"{out_path}.aria2")
        if ctrl.exists():
            try:
                ctrl.unlink()
            except Exception:
                pass
        return True

    _cleanup_aria2c_artifacts(out_path)
    return False


class VideoDownloader:
    def __init__(self, download_dir: Path = settings.download_dir):
        self.download_dir = download_dir
        try:
            if self.download_dir.is_file():
                self.download_dir.unlink()
            self.download_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _get_common_opts(self, url: str = None, use_proxy: bool | None = None) -> dict:
        opts = {
            'quiet': True,
            'no_warnings': True,
            'logger': MyLogger(),
            'buffersize': 4 * 1024 * 1024,
            'retries': 5,
            'fragment_retries': 5,
        }
        is_social = _is_known_social_url(url)
        if use_proxy is None:
            # Generic websites run direct first for full VPS bandwidth; social media keeps existing WARP behavior
            use_proxy = is_social

        # Use direct connection for YouTube (bypasses slow proxy throttling with authenticated cookies)
        if use_proxy and is_warp_proxy_available() and not (url and ("youtube.com" in url or "youtu.be" in url)):
            opts['proxy'] = 'socks5://127.0.0.1:40000'
        elif is_social:
            opts['concurrent_fragment_downloads'] = 8

        if url and ("youtube.com" in url or "youtu.be" in url):
            opts['http_chunk_size'] = 1024 * 1024 * 10
            opts['concurrent_fragment_downloads'] = 12
            opts['format_sort'] = ['res', 'proto:https']
            opts['remote_components'] = ['ejs:github']
            opts['extractor_args'] = {
                'youtubepot-bgutilhttp': {
                    'base_url': ['http://127.0.0.1:4416']
                }
            }
        elif url and not is_social:
            # Generic website speed + stability: parallel fragments, chunked HTTPS, no proxy bottleneck
            opts['noplaylist'] = True
            opts['geo_bypass'] = True
            opts['socket_timeout'] = 20
            opts['http_chunk_size'] = 1024 * 1024 * 10
            opts['concurrent_fragment_downloads'] = 16
            opts['format_sort'] = ['res', 'proto:https']
            opts['http_headers'] = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
            }

        # Look for cookies.txt in the same directory as this file or working directory
        cookie_file = Path(__file__).parent / "cookies.txt"
        if not cookie_file.exists():
            cookie_file = Path("cookies.txt")
            
        if cookie_file.exists():
            if is_valid_netscape_cookie_file(cookie_file):
                opts['cookiefile'] = str(cookie_file.resolve())
            else:
                logger.warning(f"Ignoring invalid / non-Netscape cookie file: {cookie_file}")

        return opts

    def _extract_info(self, url: str, use_proxy: bool | None = None) -> dict:
        """
        Synchronous extraction of video metadata using yt-dlp.
        """
        ydl_opts = self._get_common_opts(url, use_proxy=use_proxy)
        ydl_opts['skip_download'] = True
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)

    async def get_video_info(self, url: str) -> dict:
        """
        Asynchronous wrapper to get video metadata.
        Supports TikTok slideshows via TikWM API fallback.
        """
        # Resolve redirects for short links first (e.g. vt.tiktok.com, pin.it)
        url = await asyncio.to_thread(resolve_url, url)

        # Check TikWM API first for any TikTok link
        tik_data = None
        if "tiktok.com" in url:
            try:
                logger.info(f"Pre-fetching TikWM data for TikTok link: {url}")
                tik_data = await asyncio.to_thread(fetch_tikwm_data, url)
                if tik_data:
                    images = tik_data.get("images")
                    if images:
                        logger.info("Detected TikTok slideshow post via TikWM images list.")
                        return {
                            'is_slideshow': True,
                            'title': tik_data.get('title', 'TikTok Slideshow'),
                            'images': images,
                            'audio': tik_data.get('music'),
                            'tikwm_video_url': tik_data.get('play'),
                            'platform': 'tiktok',
                            'uploader': tik_data.get('author', {}).get('nickname', 'TikTok Creator')
                        }
                    elif tik_data.get("play"):
                        logger.info("Detected TikTok video post via TikWM play URL.")
                        return {
                            'is_slideshow': False,
                            'title': tik_data.get('title', 'TikTok Video'),
                            'duration': tik_data.get('duration'),
                            'uploader': tik_data.get('author', {}).get('nickname', 'TikTok Creator'),
                            'platform': 'tiktok',
                            'url': url,
                            'tikwm_video_url': tik_data.get('play')
                        }
            except Exception as e:
                logger.error(f"TikWM pre-fetch failed: {e}")

        # Try default yt-dlp extraction
        try:
            try:
                info = await asyncio.to_thread(self._extract_info, url)
            except Exception:
                if not _is_known_social_url(url) and is_warp_proxy_available():
                    logger.info(f"Direct extraction failed for generic site, retrying via WARP proxy: {url}")
                    info = await asyncio.to_thread(self._extract_info, url, True)
                else:
                    raise

            # Check if yt-dlp extracted playlist / carousel entries
            if info:
                entries = info.get("entries")
                if entries:
                    entry_list = [item for item in entries if isinstance(item, dict)]
                    first_entry = entry_list[0] if entry_list else None
                    has_video_entry = bool(
                        first_entry and (
                            first_entry.get("formats")
                            or str(first_entry.get("ext", "")).lower() in {"mp4", "webm", "mkv", "mov", "flv", "m3u8", "ts"}
                        )
                    )
                    if has_video_entry:
                        # Unwrap single/first video from generic HTML5/embed playlist entries
                        if not first_entry.get("title") and info.get("title"):
                            first_entry["title"] = info["title"]
                        if not first_entry.get("uploader") and info.get("uploader"):
                            first_entry["uploader"] = info["uploader"]
                        info = first_entry
                    else:
                        img_urls = []
                        for item in entry_list:
                            u = item.get("url") or item.get("display_url") or item.get("thumbnail")
                            if u:
                                img_urls.append(u)
                        if img_urls:
                            info['is_slideshow'] = True
                            info['images'] = img_urls
                            info['platform'] = info.get('extractor_key', 'instagram').lower()
                            info['title'] = info.get('title') or info.get('description') or 'Instagram Album'
                elif "instagram.com" in url or "instagr.am" in url:
                    ext = str(info.get("ext", "")).lower()
                    vcodec = str(info.get("vcodec", "")).lower()
                    if ext in ["jpg", "jpeg", "png", "webp", "heic"] or vcodec == "none":
                        img_url = info.get("url") or info.get("display_url") or info.get("thumbnail")
                        if img_url:
                            info['is_slideshow'] = True
                            info['images'] = [img_url]
                            info['platform'] = 'instagram'
                            info['title'] = info.get('title') or info.get('description') or 'Instagram Photo'

                # If yt-dlp returned info without video formats or entries for Instagram, try Instagram fallback
                if ("instagram.com" in url or "instagr.am" in url) and not info.get("formats") and not info.get("is_slideshow"):
                    insta_data = await asyncio.to_thread(fetch_instagram_image, url)
                    if insta_data and insta_data.get("images"):
                        return insta_data

                # Fix Instagram generic 'Video by <user>' title -> use real post caption
                if "instagram.com" in url or "instagr.am" in url:
                    ig_title = (info.get("title") or "").strip()
                    is_generic_ig = (
                        not ig_title
                        or re.match(r'^(?:video|post|reel)\s+by\s+', ig_title, re.IGNORECASE)
                        or ig_title.lower().startswith("instagram post (")
                        or ig_title.lower() in {"instagram", "instagram video", "instagram reel", "instagram album", "instagram photo"}
                    )
                    if is_generic_ig:
                        ig_desc = (info.get("description") or "").strip()
                        if ig_desc:
                            info["title"] = ig_desc
                        else:
                            scraped_ig_caption = await asyncio.to_thread(fetch_instagram_caption, url)
                            if scraped_ig_caption:
                                info["title"] = scraped_ig_caption

                # Fix Facebook generic / missing titles by extracting real post caption
                if "facebook.com" in url or "fb.watch" in url or "fb.me" in url:
                    fb_uploader = info.get("uploader") or info.get("channel")
                    raw_fb_title = _clean_facebook_caption(info.get("title") or "", uploader=fb_uploader)
                    if _is_generic_fb_title(raw_fb_title):
                        desc_cand = _clean_facebook_caption(info.get("description") or info.get("alt_title") or "", uploader=fb_uploader)
                        if desc_cand and not _is_generic_fb_title(desc_cand):
                            info["title"] = desc_cand
                        else:
                            scraped_caption = await asyncio.to_thread(fetch_facebook_caption, url, fb_uploader)
                            if scraped_caption:
                                info["title"] = scraped_caption
                    else:
                        info["title"] = raw_fb_title

            return info
        except Exception as e:
            # Fallback to Instagram image extractor if it is an Instagram link
            if "instagram.com" in url or "instagr.am" in url:
                logger.info(f"yt-dlp failed to extract Instagram. Attempting Instagram image fallback for: {url}")
                insta_data = await asyncio.to_thread(fetch_instagram_image, url)
                if insta_data and insta_data.get("images"):
                    return insta_data

            # Fallback to TikWM API for any TikTok extraction issues
            if "tiktok.com" in url:
                logger.info(f"yt-dlp failed to extract TikTok. Attempting TikWM API fallback for: {url}")
                if not tik_data:
                    try:
                        tik_data = await asyncio.to_thread(fetch_tikwm_data, url)
                    except Exception as tik_err:
                        logger.error(f"TikWM fallback fetch failed: {tik_err}")
                if tik_data:
                    images = tik_data.get("images")
                    if images:
                        return {
                            'is_slideshow': True,
                            'title': tik_data.get('title', 'TikTok Slideshow'),
                            'images': images,
                            'audio': tik_data.get('music'),
                            'tikwm_video_url': tik_data.get('play'),
                            'platform': 'tiktok',
                            'uploader': tik_data.get('author', {}).get('nickname', 'TikTok Creator')
                        }
                    else:
                        # Video extraction success via TikWM
                        return {
                            'is_slideshow': False,
                            'title': tik_data.get('title', 'TikTok Video'),
                            'duration': tik_data.get('duration'),
                            'uploader': tik_data.get('author', {}).get('nickname', 'TikTok Creator'),
                            'platform': 'tiktok',
                            'url': url,
                            'tikwm_video_url': tik_data.get('play')
                        }
            # Fallback to Pinterest image extractor if it is a Pinterest link
            if "pinterest.com" in url or "pin.it" in url:
                logger.info(f"yt-dlp failed to extract Pinterest. Attempting Pinterest image fallback: {url}")
                pin_data = await asyncio.to_thread(fetch_pinterest_image, url)
                if pin_data and pin_data.get("image_url"):
                    return pin_data
            
            # Fallback to Invidious API for any YouTube extraction / bot-check issues
            if "youtube.com" in url or "youtu.be" in url:
                logger.info(f"yt-dlp failed to extract YouTube. Attempting Invidious fallback for: {url}")
                yt_fallback_data = await asyncio.to_thread(fetch_invidious_youtube_data, url)
                if yt_fallback_data and (yt_fallback_data.get("hd_url") or yt_fallback_data.get("sd_url") or yt_fallback_data.get("audio_url")):
                    return yt_fallback_data

            # Log only if both yt-dlp and fallback failed (or no fallback exists)
            logger.error(f"Failed to analyze URL {url}: {e}")
            raise e

    def _cleanup_prefix_files(self, filename_prefix: str):
        """Remove partial or temporary downloaded files matching filename_prefix."""
        try:
            for item in self.download_dir.iterdir():
                if item.is_file() and item.name.startswith(filename_prefix):
                    try:
                        item.unlink()
                    except Exception:
                        pass
        except Exception:
            pass

    def _try_aria2c_info(self, ydl, info: dict, progress_hook=None, cancel_event=None) -> str | None:
        """
        Attempt multi-connection aria2c download from already-extracted yt-dlp info.
        Returns output path on success or None to fall back to native yt-dlp.
        """
        if not info or not isinstance(info, dict) or info.get("entries"):
            return None

        target_file = ydl.prepare_filename(info)
        req_fmts = info.get("requested_formats")

        # Split video + audio HTTPS streams (e.g. YouTube 1080p)
        if (
            isinstance(req_fmts, list)
            and len(req_fmts) == 2
            and all(str(f.get("protocol") or "").lower() in ("http", "https") and f.get("url") for f in req_fmts)
        ):
            base, _ = os.path.splitext(target_file)
            part_v = f"{base}_v.{req_fmts[0].get('ext', 'mp4')}"
            part_a = f"{base}_a.{req_fmts[1].get('ext', 'm4a')}"
            merged_mp4 = f"{base}.mp4"
            try:
                ok_v = _download_file_aria2c(
                    req_fmts[0]["url"],
                    req_fmts[0].get("http_headers") or info.get("http_headers"),
                    part_v,
                    progress_hook=progress_hook,
                    cancel_event=cancel_event,
                )
                if not ok_v:
                    return None
                ok_a = _download_file_aria2c(
                    req_fmts[1]["url"],
                    req_fmts[1].get("http_headers") or info.get("http_headers"),
                    part_a,
                    progress_hook=progress_hook,
                    cancel_event=cancel_event,
                )
                if not ok_a:
                    return None
                if cancel_event and cancel_event.is_set():
                    raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
                cmd = [
                    "ffmpeg", "-y",
                    "-i", part_v,
                    "-i", part_a,
                    "-c", "copy",
                    "-movflags", "+faststart",
                    merged_mp4,
                ]
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if res.returncode == 0 and os.path.exists(merged_mp4) and os.path.getsize(merged_mp4) > 0:
                    return merged_mp4
                if os.path.exists(merged_mp4):
                    try:
                        os.remove(merged_mp4)
                    except Exception:
                        pass
                return None
            finally:
                for tmp in (part_v, part_a):
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass

        # Single progressive HTTP/HTTPS stream (or upgrade equal-height HLS to progressive MP4 if available)
        proto = str(info.get("protocol") or "").lower()
        media_url = info.get("url")
        http_headers = info.get("http_headers")
        if proto not in ("http", "https") and not req_fmts and info.get("formats"):
            target_h = info.get("height")
            for cand in info["formats"]:
                if not isinstance(cand, dict):
                    continue
                c_proto = str(cand.get("protocol") or "").lower()
                if (
                    c_proto in ("http", "https")
                    and cand.get("url")
                    and cand.get("vcodec") != "none"
                    and cand.get("acodec") != "none"
                    and (not target_h or cand.get("height") == target_h)
                ):
                    media_url = cand["url"]
                    proto = c_proto
                    http_headers = cand.get("http_headers") or http_headers
                    base, _ = os.path.splitext(target_file)
                    target_file = f"{base}.{cand.get('ext', 'mp4')}"
                    break

        if media_url and proto in ("http", "https") and not req_fmts:
            if _download_file_aria2c(
                media_url,
                http_headers,
                target_file,
                progress_hook=progress_hook,
                cancel_event=cancel_event,
            ):
                return target_file
        return None

    def _download_video(self, url: str, format_spec: str, output_template: str, progress_hook=None, cancel_event=None) -> str:
        """
        Synchronous download of video using yt-dlp with specified format.
        Uses 16-connection aria2c acceleration for direct HTTP/HTTPS streams and falls back to native yt-dlp.
        """
        # Try requested format first, then fallback to permissive formats
        format_attempts = []
        for fmt in [format_spec, 'bv*+ba/b/best', 'b/best']:
            if fmt and fmt not in format_attempts:
                format_attempts.append(fmt)
        
        last_error = None
        proxy_modes = [False, True] if (not _is_known_social_url(url) and is_warp_proxy_available()) else [True]
        for idx, use_proxy in enumerate(proxy_modes):
            for fmt in format_attempts:
                if cancel_event and cancel_event.is_set():
                    raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
                ydl_opts = self._get_common_opts(url, use_proxy=use_proxy)
                ydl_opts.update({
                    'format': fmt,
                    'outtmpl': output_template,
                    'noprogress': True,
                    'merge_output_format': 'mp4',
                })
                
                if progress_hook:
                    ydl_opts['progress_hooks'] = [progress_hook]

                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        if cancel_event and cancel_event.is_set():
                            raise yt_dlp.utils.DownloadCancelled("Cancelled by user")

                        # Accelerate progressive HTTPS downloads via aria2c when not routed through SOCKS5 WARP
                        if shutil.which("aria2c") and not ydl_opts.get("proxy"):
                            fast_path = self._try_aria2c_info(
                                ydl, info, progress_hook=progress_hook, cancel_event=cancel_event
                            )
                            if fast_path and os.path.exists(fast_path):
                                return fast_path

                        # Native yt-dlp download from already-extracted info (avoids duplicate webpage scrape)
                        ydl.process_info(info)
                        if cancel_event and cancel_event.is_set():
                            raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
                        filename = ydl.prepare_filename(info)
                        if os.path.exists(filename):
                            return filename
                        
                        # Fallback if filename extension changed (e.g. merged video or images)
                        base, _ = os.path.splitext(filename)
                        for ext in ['.mp4', '.mkv', '.webm', '.m4a', '.mp3', '.jpg', '.jpeg', '.png', '.webp']:
                            if os.path.exists(base + ext):
                                return base + ext
                        
                        raise FileNotFoundError(f"Downloaded file not found for template {output_template}")
                except yt_dlp.utils.DownloadCancelled:
                    raise
                except Exception as e:
                    last_error = e
                    if "Requested format is not available" in str(e) and fmt != format_attempts[-1]:
                        logger.warning(f"Format '{fmt}' not available, trying fallback...")
                        continue
                    if idx == len(proxy_modes) - 1:
                        raise
                    break
                finally:
                    if cancel_event and cancel_event.is_set():
                        tmpl_name = os.path.basename(output_template)
                        stem_prefix = tmpl_name.split("%(")[0]
                        if stem_prefix:
                            self._cleanup_prefix_files(stem_prefix)
            if last_error and idx < len(proxy_modes) - 1:
                logger.warning(f"Direct download failed for generic site, retrying via WARP proxy: {last_error}")
                continue
        
        raise last_error

    async def create_slideshow_video(self, images: list, audio_url: str, output_path: Path, progress_callback=None) -> str:
        import urllib.request
        import subprocess
        import tempfile
        import shutil
        import json

        temp_dir = Path(tempfile.mkdtemp(prefix="tiktok_slideshow_"))
        def download_file(u, p):
            u = u.strip()
            if not (u.startswith('http://') or u.startswith('https://')):
                m = re.search(r'https?://[^\s,\'\"]+', u)
                if m:
                    u = m.group(0)
                else:
                    raise ValueError(f"Invalid URL without scheme: {u}")
            req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=10) as resp, open(p, 'wb') as f:
                f.write(resp.read())

        try:
            # Calculate estimated total size
            num_images = len(images)
            est_audio_size = 1024 * 1024  # 1MB
            est_image_size = 200 * 1024   # 200KB per image
            total_est_size = est_audio_size + (num_images * est_image_size)
            downloaded_bytes = 0

            # 1. Download audio
            audio_path = temp_dir / "audio.mp3"
            if audio_url:
                if progress_callback:
                    await progress_callback(5, 0, total_est_size, 0)
                try:
                    await asyncio.to_thread(download_file, audio_url, audio_path)
                    downloaded_bytes += est_audio_size
                except Exception as audio_err:
                    logger.error(f"Failed to download audio track: {audio_err}")
                if progress_callback:
                    await progress_callback(15, downloaded_bytes, total_est_size, 0)

            # Get audio duration
            audio_duration = 15.0  # default
            if audio_url and audio_path.exists():
                try:
                    def get_duration():
                        cmd = [
                            'ffprobe', '-v', 'error',
                            '-show_entries', 'format=duration',
                            '-of', 'default=noprint_wrappers=1:nokey=1',
                            str(audio_path)
                        ]
                        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
                        return float(res.stdout.strip())
                    audio_duration = await asyncio.to_thread(get_duration)
                except Exception as e:
                    logger.error(f"Failed to probe audio duration: {e}")

            # 2. Download raw images concurrently
            raw_image_paths = [None] * num_images
            
            async def download_one(img_url, idx):
                nonlocal downloaded_bytes
                raw_img = temp_dir / f"raw_{idx}.jpg"
                try:
                    await asyncio.to_thread(download_file, img_url, raw_img)
                    raw_image_paths[idx] = raw_img
                    downloaded_bytes += est_image_size
                    if progress_callback:
                        pct = 15 + int((idx + 1) / num_images * 55)  # 15% to 70%
                        await progress_callback(pct, downloaded_bytes, total_est_size, 0)
                except Exception as err:
                    logger.error(f"Failed to download image {idx}: {err}")

            # Run download tasks concurrently
            download_tasks = [download_one(url, idx) for idx, url in enumerate(images)]
            await asyncio.gather(*download_tasks)

            # Filter valid raw images
            valid_raw_images = [path for path in raw_image_paths if path and path.exists()]
            if not valid_raw_images:
                raise ValueError("No slideshow images could be downloaded.")

            # Calculate slide duration to cover the full audio duration
            slide_dur = audio_duration / len(valid_raw_images)
            if slide_dur < 1.0:
                slide_dur = 1.0

            if progress_callback:
                await progress_callback(75, downloaded_bytes, total_est_size, 0)

            # 3. Merge into video using FFmpeg in a single call with -preset ultrafast
            cmd = ['ffmpeg', '-y']
            for path in valid_raw_images:
                cmd.extend(['-loop', '1', '-t', f'{slide_dur:.3f}', '-i', str(path)])
            
            if audio_url and audio_path.exists():
                cmd.extend(['-i', str(audio_path)])
            
            # Construct filter complex
            filters = []
            for idx in range(len(valid_raw_images)):
                filters.append(f"[{idx}:v]scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1[v{idx}]")
            
            concat_inputs = "".join(f"[v{idx}]" for idx in range(len(valid_raw_images)))
            filters.append(f"{concat_inputs}concat=n={len(valid_raw_images)}:v=1:a=0[outv]")
            
            filter_str = ";".join(filters)
            
            cmd.extend([
                '-filter_complex', filter_str,
                '-map', '[outv]'
            ])
            
            if audio_url and audio_path.exists():
                audio_idx = len(valid_raw_images)
                cmd.extend(['-map', f'{audio_idx}:a', '-c:a', 'aac', '-shortest'])
            
            cmd.extend([
                '-pix_fmt', 'yuv420p',
                '-c:v', 'libx264',
                '-r', '25',
                '-preset', 'ultrafast',
                str(output_path)
            ])

            # Run FFmpeg process asynchronously
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()

            return str(output_path.resolve())
        finally:
            try:
                shutil.rmtree(temp_dir)
            except Exception as cleanup_err:
                logger.error(f"Failed to delete temp dir {temp_dir}: {cleanup_err}")

    async def download(self, url: str, format_spec: str, filename_prefix: str, progress_callback=None, tikwm_video_url=None, is_slideshow=False, images=None, audio=None, direct_url=None, cancel_event=None) -> str:
        """
        Asynchronously download video, audio, or direct stream URLs.
        """
        if cancel_event and cancel_event.is_set():
            raise yt_dlp.utils.DownloadCancelled("Cancelled by user")

        # If it is a slideshow request, render the video using FFmpeg!
        if is_slideshow and images:
            output_path = self.download_dir / f"{filename_prefix}_slideshow.mp4"
            if progress_callback:
                # Notify progress callback of initialization
                await progress_callback(0, 0, 1, 0)
            res_path = await self.create_slideshow_video(images, audio, output_path, progress_callback)
            if cancel_event and cancel_event.is_set():
                self._cleanup_prefix_files(filename_prefix)
                raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
            if progress_callback:
                await progress_callback(100, 1, 1, 0)
            return res_path

        # If we have a direct download URL (e.g. Invidious fallback / direct stream), download directly
        target_direct_url = direct_url or tikwm_video_url or (audio if not is_slideshow else None)
        if target_direct_url:
            is_audio_only = (target_direct_url == audio) or (bool(format_spec) and format_spec.startswith("bestaudio") and "bestvideo" not in format_spec)
            ext = ".mp3" if is_audio_only else ".mp4"
            output_path = self.download_dir / f"{filename_prefix}_direct{ext}"
            loop = asyncio.get_running_loop()
            
            def download_stream():
                if shutil.which("aria2c"):
                    def aria_hook(d):
                        if progress_callback:
                            dl_b = d.get("downloaded_bytes", 0)
                            tot_b = d.get("total_bytes", 0)
                            spd = d.get("speed", 0)
                            pct = (dl_b / tot_b * 100) if tot_b > 0 else 0
                            asyncio.run_coroutine_threadsafe(
                                progress_callback(pct, dl_b, tot_b, spd),
                                loop
                            )
                    ok = _download_file_aria2c(
                        target_direct_url,
                        {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                        output_path,
                        progress_hook=aria_hook if progress_callback else None,
                        cancel_event=cancel_event,
                    )
                    if ok:
                        return str(output_path.resolve())

                req = urllib.request.Request(target_direct_url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                try:
                    with urllib.request.urlopen(req) as response:
                        total_size = int(response.headers.get('content-length', 0))
                        downloaded = 0
                        block_size = 1024 * 512
                        
                        with open(output_path, 'wb') as out_file:
                            start_time = time.time()
                            while True:
                                if cancel_event and cancel_event.is_set():
                                    raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
                                buffer = response.read(block_size)
                                if not buffer:
                                    break
                                downloaded += len(buffer)
                                out_file.write(buffer)
                                
                                if progress_callback:
                                    percent = (downloaded / total_size * 100) if total_size > 0 else 0
                                    elapsed = time.time() - start_time
                                    speed = downloaded / elapsed if elapsed > 0 else 0
                                    asyncio.run_coroutine_threadsafe(
                                        progress_callback(percent, downloaded, total_size, speed),
                                        loop
                                    )
                    return str(output_path.resolve())
                except yt_dlp.utils.DownloadCancelled:
                    if output_path.exists():
                        try:
                            output_path.unlink()
                        except Exception:
                            pass
                    raise
                
            return await asyncio.to_thread(download_stream)

        output_template = str(self.download_dir / f"{filename_prefix}_%(id)s.%(ext)s")
        loop = asyncio.get_running_loop()

        def hook(d):
            if cancel_event and cancel_event.is_set():
                raise yt_dlp.utils.DownloadCancelled("Cancelled by user")
            if progress_callback and d['status'] == 'downloading':
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                speed = d.get('speed', 0)
                percent = (downloaded / total * 100) if total > 0 else 0
                asyncio.run_coroutine_threadsafe(
                    progress_callback(percent, downloaded, total, speed),
                    loop
                )

        try:
            filepath = await asyncio.to_thread(
                self._download_video,
                url,
                format_spec,
                output_template,
                hook if (progress_callback or cancel_event) else None,
                cancel_event,
            )
            return filepath
        except yt_dlp.utils.DownloadCancelled:
            self._cleanup_prefix_files(filename_prefix)
            raise
