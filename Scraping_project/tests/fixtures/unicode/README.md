# Unicode and charset fixtures (#664)

Offline HTML bodies for spider `parse` tests. Read them with `Path.read_bytes`
and let Scrapy discover the charset. Do not open them as locale text.

| File | Bytes | What it covers |
|---|---|---|
| `utf8_mixed.html` | UTF-8 | Non-ASCII punctuation, emoji (ZWJ), NFC and NFD paths, fullwidth letters, RTL text, IDN host, JSON-LD and `decodeURIComponent` |
| `windows1252_declared.html` | windows-1252 | Declared alternate charset (`<meta charset>` and a matching Content-Type). Euro and em dash are cp1252, not Latin-1 |
| `malformed_utf8.html` | UTF-8 plus raw `0xFF` | Malformed-but-recoverable input. Undecodable bytes become U+FFFD; invalid percent escapes stay escaped |

`windows1252_declared.html` and `malformed_utf8.html` are binary (`-text` in `.gitattributes`).
