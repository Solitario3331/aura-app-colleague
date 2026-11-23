"""Downloads Geist + Geist Mono (variable weight 100-900, latin) as woff2 into this folder."""
import pathlib, urllib.request

HERE = pathlib.Path(__file__).parent
BASE = "https://cdn.jsdelivr.net/npm/@fontsource-variable/"
FILES = {
    "Geist-Variable.woff2": BASE + "geist/files/geist-latin-wght-normal.woff2",
    "GeistMono-Variable.woff2": BASE + "geist-mono/files/geist-mono-latin-wght-normal.woff2",
}
for name, url in FILES.items():
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=30).read()
    assert data[:4] == b"wOF2", name + " is not a woff2 file"
    (HERE / name).write_bytes(data)
    print(name, len(data), "bytes")
