from pathlib import Path
text = Path('Scraping_project/README.md').read_text(encoding='utf-8')
print(text.count('`'))
idx = text.index('`')
print([ord(c) for c in text[idx:idx+5]])
