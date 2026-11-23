"""Downloads the Nous/Hermes-Agent theme fonts (Oswald, EB Garamond italic) (latin subset woff2) from Google Fonts into this folder."""
import pathlib, re, urllib.request

HERE = pathlib.Path(__file__).parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
CSS = ("https://fonts.googleapis.com/css2?family=Oswald:wght@400..700"
       "&family=EB+Garamond:ital,wght@1,400..600&display=swap")


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read()


css = get(CSS).decode()
blocks = re.findall(r"/\* (\S+) \*/\s*@font-face \{(.*?)\}", css, re.S)
for subset, body in blocks:
    if subset != "latin":
        continue
    fam = re.search(r"font-family: '([^']+)'", body).group(1).replace(" ", "")
    style = re.search(r"font-style: (\w+)", body).group(1)
    weight = re.search(r"font-weight: ([\d ]+);", body).group(1).replace(" ", "-")
    url = re.search(r"url\((\S+?)\)", body).group(1)
    name = f"{fam}-{weight}{'-italic' if style == 'italic' else ''}.woff2"
    data = get(url)
    (HERE / name).write_bytes(data)
    print(name, len(data))
