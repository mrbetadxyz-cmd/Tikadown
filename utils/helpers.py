import ipaddress
import re
from urllib.parse import urlparse

# Regular expressions for URL validation and platform detection
TIKTOK_REGEX = re.compile(r'(https?://)?(www\.)?(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com|link\.tiktok\.com)/.*')
INSTAGRAM_REGEX = re.compile(r'(https?://)?(www\.)?(instagram\.com|instagr\.am)/(p|reel|tv|stories)/.*')
PINTEREST_REGEX = re.compile(r'(https?://)?(www\.)?([a-zA-Z0-9-]+\.)?(pinterest\.com|pin\.it)/.*')
FACEBOOK_REGEX = re.compile(r'(https?://)?(www\.|web\.|m\.)?(facebook\.com|fb\.watch|fb\.com)/.*')
YOUTUBE_REGEX = re.compile(r'(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be)/.*')
TWITTER_REGEX = re.compile(r'(https?://)?(www\.)?(twitter\.com|x\.com)/.*')
SOUNDCLOUD_REGEX = re.compile(r'(https?://)?(www\.)?(soundcloud\.com|snd\.sc)/.*')


def is_safe_public_url(url: str) -> bool:
    """
    Validates that url uses http/https and targets a public hostname/IP
    rather than loopback, private, link-local, or internal network addresses.
    """
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or "." not in host:
        return False
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".internal")):
        return False
    try:
        ip_obj = ipaddress.ip_address(host)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return False
    except ValueError:
        pass
    return True


def _extract_site_label(url: str) -> str:
    """Extracts a clean short platform label from a generic URL's hostname."""
    host = (urlparse(url.strip()).hostname or "").lower()
    for prefix in ("www.", "m.", "mobile.", "v."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    label = re.sub(r"[^a-z0-9]", "", host.split(".")[0])
    return label[:20] if label else "web"


def get_platform(url: str) -> str | None:
    """
    Detect the platform (TikTok, Instagram, Pinterest, Facebook, YouTube, Twitter/X, SoundCloud)
    or return a clean generic site label for any other valid public http/https URL.
    """
    url = url.strip()
    if TIKTOK_REGEX.match(url):
        return "tiktok"
    elif INSTAGRAM_REGEX.match(url):
        return "instagram"
    elif PINTEREST_REGEX.match(url):
        return "pinterest"
    elif FACEBOOK_REGEX.match(url):
        return "facebook"
    elif YOUTUBE_REGEX.match(url):
        return "youtube"
    elif TWITTER_REGEX.match(url):
        return "twitter"
    elif SOUNDCLOUD_REGEX.match(url):
        return "soundcloud"
    elif is_safe_public_url(url):
        return _extract_site_label(url)
    return None

def format_size(bytes_size: int) -> str:
    """
    Format bytes to a human-readable string.
    """
    if bytes_size is None:
        return "Unknown size"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

def clean_url(url: str) -> str:
    """
    Basic URL cleanup (strip whitespace).
    """
    return url.strip()
