"""
Stage 1: URL Discovery with Rich Metadata
Gathers ALL URLs and identifies what kind of URL it is.
"""

import logging
from urllib.parse import urlparse
from typing import Optional

from scrapy.http import Response

logger = logging.getLogger(__name__)


def identify_url_type(url: str, response: Optional[Response] = None) -> dict:
    """
    Identify what kind of URL this is based on path and content-type.

    Returns metadata about the URL to help stage 2 process it correctly.
    """
    parsed = urlparse(url)
    path = parsed.path.lower()

    metadata = {
        'url': url,
        'domain': parsed.netloc,
        'path': parsed.path,
        'type': 'unknown',
        'has_media': False,
        'file_extension': None,
        'content_type': None
    }

    # Get file extension
    if '.' in path:
        ext = path.split('.')[-1]
        metadata['file_extension'] = ext

    # Identify by extension
    image_exts = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg']
    audio_exts = ['mp3', 'wav', 'm4a', 'ogg', 'flac', 'aac']
    video_exts = ['mp4', 'avi', 'mov', 'wmv', 'webm', 'mkv']
    doc_exts = ['pdf', 'doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx']

    ext = metadata['file_extension']
    if ext in image_exts:
        metadata['type'] = 'image'
        metadata['has_media'] = True
    elif ext in audio_exts:
        metadata['type'] = 'audio'
        metadata['has_media'] = True
    elif ext in video_exts:
        metadata['type'] = 'video'
        metadata['has_media'] = True
    elif ext in doc_exts:
        metadata['type'] = 'document'
    elif ext in ['html', 'htm', 'php', 'asp', 'jsp'] or not ext:
        metadata['type'] = 'webpage'

    # Get content-type from response if available
    if response:
        content_type = response.headers.get('Content-Type', b'').decode('utf-8', errors='ignore')
        metadata['content_type'] = content_type

        # Override type based on content-type
        if 'image/' in content_type:
            metadata['type'] = 'image'
            metadata['has_media'] = True
        elif 'audio/' in content_type:
            metadata['type'] = 'audio'
            metadata['has_media'] = True
        elif 'video/' in content_type:
            metadata['type'] = 'video'
            metadata['has_media'] = True
        elif 'application/pdf' in content_type:
            metadata['type'] = 'document'
        elif 'text/html' in content_type:
            metadata['type'] = 'webpage'

    # Identify by path patterns
    if '/api/' in path or '/v1/' in path or path.endswith('.json'):
        metadata['is_api'] = True
    else:
        metadata['is_api'] = False

    if '/admin/' in path or '/login/' in path or '/auth/' in path:
        metadata['is_admin'] = True
    else:
        metadata['is_admin'] = False

    return metadata


def extract_page_metadata(response: Response) -> dict:
    """Extract additional metadata from HTML page."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.text, 'html.parser')

    metadata = {
        'title': None,
        'meta_description': None,
        'meta_keywords': None,
        'has_images': False,
        'has_audio': False,
        'has_video': False,
        'image_count': 0,
        'audio_count': 0,
        'video_count': 0,
        'link_count': 0,
        'word_count': 0
    }

    # Get title
    title = soup.find('title')
    if title:
        metadata['title'] = title.get_text(strip=True)

    # Get meta tags
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    if meta_desc:
        metadata['meta_description'] = meta_desc.get('content', '')

    meta_kw = soup.find('meta', attrs={'name': 'keywords'})
    if meta_kw:
        metadata['meta_keywords'] = meta_kw.get('content', '')

    # Count media
    images = soup.find_all('img')
    metadata['image_count'] = len(images)
    metadata['has_images'] = len(images) > 0

    audio = soup.find_all('audio') + soup.find_all('source', type=lambda x: x and x.startswith('audio'))
    metadata['audio_count'] = len(audio)
    metadata['has_audio'] = len(audio) > 0

    video = soup.find_all('video') + soup.find_all('source', type=lambda x: x and x.startswith('video'))
    metadata['video_count'] = len(video)
    metadata['has_video'] = len(video) > 0

    # Count links
    links = soup.find_all('a', href=True)
    metadata['link_count'] = len(links)

    # Rough word count
    text = soup.get_text()
    words = text.split()
    metadata['word_count'] = len(words)

    return metadata
