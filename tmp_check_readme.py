from pathlib import Path
text = Path('Scraping_project/README.md').read_text(encoding='utf-8')
idx = text.find('`')
print(idx)
print(text[idx:idx+12])
