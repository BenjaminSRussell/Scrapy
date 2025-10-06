"""
Stage 2: Analytics & Data Extraction
- Loads full webpage
- Removes useless content
- Extracts best information
- OCR on images
- Whisper on audio/video
- YAKE for initial categorization
"""

import logging
from typing import Optional
import httpx
from bs4 import BeautifulSoup

from src.common.constants import REQUEST_TIMEOUT, DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)


def extract_keywords(text: str, max_keywords: int = 10) -> list:
    """Extract keywords using YAKE."""
    if not text or len(text) < 50:
        return []

    try:
        import yake

        kw_extractor = yake.KeywordExtractor(
            lan="en",
            n=3,
            dedupLim=0.9,
            top=max_keywords,
        )

        keywords = kw_extractor.extract_keywords(text[:5000])
        return [kw[0] for kw in keywords]

    except Exception as e:
        logger.warning(f"YAKE failed: {e}")
        return []


def clean_html(html: str) -> tuple[str, str]:
    """
    Remove all useless content from HTML and extract the best information.

    Returns:
        (title, cleaned_text)
    """
    soup = BeautifulSoup(html, 'html.parser')

    # Get title
    title = soup.find('title')
    title_text = title.get_text(strip=True) if title else "Untitled"

    # Remove useless elements
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside',
                     'iframe', 'noscript', 'form', 'button']):
        tag.decompose()

    # Get main content
    main = soup.find('main') or soup.find('article') or soup.find('div', class_=lambda x: x and 'content' in x.lower())

    if main:
        text = main.get_text(separator=' ', strip=True)
    else:
        text = soup.get_text(separator=' ', strip=True)

    # Clean up whitespace
    text = ' '.join(text.split())

    return title_text, text


def process_image_ocr(image_url: str) -> Optional[str]:
    """Extract text from image using OCR."""
    try:
        import easyocr
        import tempfile
        from pathlib import Path

        # Download image
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(image_url, headers={"User-Agent": DEFAULT_USER_AGENT})
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

        # Run OCR
        reader = easyocr.Reader(['en'], gpu=False)
        results = reader.readtext(tmp_path)
        text = ' '.join([result[1] for result in results])

        # Cleanup
        Path(tmp_path).unlink()

        return text if text.strip() else None

    except ImportError:
        logger.warning("EasyOCR not installed")
        return None
    except Exception as e:
        logger.error(f"OCR failed for {image_url}: {e}")
        return None


def process_audio_whisper(audio_url: str) -> Optional[str]:
    """Transcribe audio using Whisper."""
    try:
        import whisper
        import tempfile
        from pathlib import Path

        # Download audio
        with httpx.Client(timeout=REQUEST_TIMEOUT * 2) as client:
            response = client.get(audio_url, headers={"User-Agent": DEFAULT_USER_AGENT})
            response.raise_for_status()

            suffix = Path(audio_url).suffix or '.mp3'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

        # Transcribe
        model = whisper.load_model("base")
        result = model.transcribe(tmp_path)

        # Cleanup
        Path(tmp_path).unlink()

        return result.get("text")

    except ImportError:
        logger.warning("Whisper not installed")
        return None
    except Exception as e:
        logger.error(f"Whisper failed for {audio_url}: {e}")
        return None


def process_video_whisper(video_url: str) -> Optional[str]:
    """Extract audio from video and transcribe."""
    try:
        import whisper
        import ffmpeg
        import tempfile
        from pathlib import Path

        # Download video
        with httpx.Client(timeout=REQUEST_TIMEOUT * 3) as client:
            response = client.get(video_url, headers={"User-Agent": DEFAULT_USER_AGENT})
            response.raise_for_status()

            suffix = Path(video_url).suffix or '.mp4'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(response.content)
                video_path = tmp.name

        # Extract audio
        audio_path = video_path.replace(suffix, '.mp3')
        stream = ffmpeg.input(video_path)
        stream = ffmpeg.output(stream, audio_path, acodec='libmp3lame')
        ffmpeg.run(stream, quiet=True, overwrite_output=True)

        # Transcribe
        model = whisper.load_model("base")
        result = model.transcribe(audio_path)

        # Cleanup
        Path(video_path).unlink()
        Path(audio_path).unlink()

        return result.get("text")

    except ImportError:
        logger.warning("Whisper/ffmpeg not installed")
        return None
    except Exception as e:
        logger.error(f"Video transcription failed for {video_url}: {e}")
        return None


def analyze_url(url: str, metadata: dict) -> dict:
    """
    Analyze a URL and extract all useful data.

    Returns analytics data ready for stage 3 summarization.
    """
    analytics = {
        'url': url,
        'metadata': metadata,
        'html_text': None,
        'html_title': None,
        'ocr_texts': [],
        'audio_transcripts': [],
        'video_transcripts': [],
        'initial_categories': [],  # From YAKE
        'combined_text': None
    }

    try:
        # Fetch URL
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(url, headers={"User-Agent": DEFAULT_USER_AGENT})
            response.raise_for_status()

            content_type = response.headers.get('content-type', '')

            # Process based on type
            url_type = metadata.get('type', 'webpage')

            if url_type == 'webpage' and 'text/html' in content_type:
                # Clean HTML and extract text
                title, clean_text = clean_html(response.text)
                analytics['html_title'] = title
                analytics['html_text'] = clean_text

                # Get YAKE keywords for initial categorization
                categories = extract_keywords(clean_text, max_keywords=10)
                analytics['initial_categories'] = categories

                # If page has images, process them
                if metadata.get('has_images'):
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.text, 'html.parser')
                    images = soup.find_all('img', src=True)

                    for img in images[:5]:  # Limit to 5 images
                        img_url = response.url if img['src'].startswith('http') else url + img['src']
                        ocr_text = process_image_ocr(img_url)
                        if ocr_text:
                            analytics['ocr_texts'].append({'url': img_url, 'text': ocr_text})

            elif url_type == 'image':
                # Direct image URL
                ocr_text = process_image_ocr(url)
                if ocr_text:
                    analytics['ocr_texts'].append({'url': url, 'text': ocr_text})
                    analytics['initial_categories'] = extract_keywords(ocr_text, max_keywords=5)

            elif url_type == 'audio':
                # Direct audio URL
                transcript = process_audio_whisper(url)
                if transcript:
                    analytics['audio_transcripts'].append({'url': url, 'text': transcript})
                    analytics['initial_categories'] = extract_keywords(transcript, max_keywords=5)

            elif url_type == 'video':
                # Direct video URL
                transcript = process_video_whisper(url)
                if transcript:
                    analytics['video_transcripts'].append({'url': url, 'text': transcript})
                    analytics['initial_categories'] = extract_keywords(transcript, max_keywords=5)

        # Combine all text
        all_text = []
        if analytics['html_text']:
            all_text.append(analytics['html_text'])
        for ocr in analytics['ocr_texts']:
            all_text.append(f"Image text: {ocr['text']}")
        for audio in analytics['audio_transcripts']:
            all_text.append(f"Audio: {audio['text']}")
        for video in analytics['video_transcripts']:
            all_text.append(f"Video: {video['text']}")

        analytics['combined_text'] = '\n\n'.join(all_text)

        logger.info(f"✅ Analyzed {url}: {len(analytics['combined_text'])} chars")

    except Exception as e:
        logger.error(f"Failed to analyze {url}: {e}")
        analytics['error'] = str(e)

    return analytics
