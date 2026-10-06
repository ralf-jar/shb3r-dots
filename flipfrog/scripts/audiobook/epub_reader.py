"""Lectura de EPUB (2 y 3) sin dependencias externas -- un EPUB es un
zip con XHTML adentro. Devuelve el libro como una lista plana de bloques
(encabezados/párrafos) ya partidos en frases, más los capítulos del
índice (NCX o nav de EPUB3) apuntando al bloque donde empieza cada uno.
Usado solo por audiobook_convert.py."""

import html
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import unquote

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "x": "http://www.w3.org/1999/xhtml",
}

BLOCK_TAGS = {"p", "div", "li", "blockquote", "pre", "dt", "dd", "td", "th",
              "caption", "figcaption", "section", "article", "tr", "hr", "table"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_TAGS = {"script", "style", "head", "title", "sup", "svg", "math"}

# Encabezado/licencia de Project Gutenberg -- en inglés y fuera de la
# obra. Por texto, nunca saltando el item "pg-header" del spine: en
# varios EPUB de Gutenberg ese mismo archivo trae también el inicio de la
# obra (confirmado con La Celestina, pg1619). Dos generaciones de
# formato: la moderna ("*** START OF THE PROJECT GUTENBERG ...") y la de
# los 90 (sin START, cierra con "*SMALL PRINT! ...*END*") -- por eso se
# corta tras el ÚLTIMO bloque de boilerplate del primer tramo del libro,
# y en el primero del último tramo.
PG_HEAD_RE = re.compile(r"Project Gutenberg|\bEtexts?\b|SMALL PRINT|\*END\*", re.I)
PG_TAIL_RE = re.compile(r"\*{3}\s*END OF (THE|THIS) PROJECT GUTENBERG|End of (the|this)\b.{0,40}Project Gutenberg", re.I)
PG_EDGE = 0.2

ABBREVIATIONS = {
    "sr", "sra", "srta", "sres", "dr", "dra", "d", "dña", "ud", "uds", "vd", "vds",
    "pág", "págs", "cap", "vol", "núm", "etc", "ej", "art", "fig", "lic", "ing",
    "mr", "mrs", "ms", "st", "vs", "no", "p", "pp", "ed", "eds", "cf", "ibid",
}
SENTENCE_END_RE = re.compile(r'([.!?…]+[»”"’)\]]*)\s+(?=[¿¡«“"(\[—–-]?[A-ZÁÉÍÓÚÑÜ0-9])')
MAX_SENTENCE = 320
ROMAN_RE = re.compile(r"\b(M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3}))\b\.?")


class EpubError(Exception):
    """`code` es un código de i18n (módulo "audiolibros")."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# --- texto -------------------------------------------------------------

def _clean(text):
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _split_long(sentence):
    if len(sentence) <= MAX_SENTENCE:
        return [sentence]
    middle = len(sentence) // 2
    for sep in ("; ", ": ", ", ", " "):
        cuts = [m.end() for m in re.finditer(re.escape(sep), sentence)]
        if cuts:
            cut = min(cuts, key=lambda c: abs(c - middle))
            return _split_long(sentence[:cut].strip()) + _split_long(sentence[cut:].strip())
    return [sentence]


def split_sentences(text):
    parts = []
    start = 0
    for m in SENTENCE_END_RE.finditer(text):
        before = text[start:m.start()].rsplit(" ", 1)[-1].strip("«“\"(¿¡").lower()
        # "Sr. López", "J. R. R. Tolkien" -- no cortan frase.
        if m.group(1) == "." and (before in ABBREVIATIONS or len(before) == 1):
            continue
        parts.append(text[start:m.end(1)].strip())
        start = m.end()
    parts.append(text[start:].strip())

    sentences = []
    for part in parts:
        if re.search(r"\w", part):
            sentences.extend(_split_long(part))
    return sentences


def _roman_to_int(roman):
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, ch in enumerate(roman):
        v = values[ch]
        total += -v if i + 1 < len(roman) and values[roman[i + 1]] > v else v
    return total


def spoken_heading(text):
    """Encabezados en MAYÚSCULAS y con números romanos ("LIBRO IV.",
    "I.") -- espeak los deletrea o los lee como palabra ("i"). Solo se
    aplica a encabezados: en el cuerpo "I" puede ser palabra real."""
    def repl(m):
        roman = m.group(1)
        return str(_roman_to_int(roman)) + "." if roman else m.group(0)

    spoken = ROMAN_RE.sub(repl, text)
    if spoken.isupper():
        spoken = spoken.lower().capitalize()
    return spoken


# --- HTML --------------------------------------------------------------

class _BlockParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.anchors = {}
        self._buf = []
        self._skip = 0
        self._heading = 0

    def _flush(self):
        text = _clean("".join(self._buf))
        self._buf = []
        if text and re.search(r"\w", text):
            self.blocks.append({"type": "h" if self._heading else "p",
                                "level": self._heading or 0, "text": text})

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        anchor = attrs.get("id") or (attrs.get("name") if tag == "a" else None)
        if anchor:
            self._flush()
            self.anchors.setdefault(anchor, len(self.blocks))
        if tag in SKIP_TAGS or "noteref" in (attrs.get("epub:type") or ""):
            self._skip += 1
        elif tag in HEADING_TAGS:
            self._flush()
            self._heading = int(tag[1])
        elif tag in BLOCK_TAGS:
            self._flush()
        elif tag == "br":
            self._buf.append(" ")
        elif tag == "img" and attrs.get("alt") and self._heading:
            self._buf.append(attrs["alt"])

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in HEADING_TAGS:
            self._flush()
            self._heading = 0
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


# --- estructura del EPUB -------------------------------------------------

def _xml(zf, path):
    try:
        return ET.fromstring(zf.read(path))
    except (KeyError, ET.ParseError):
        return None


def _resolve(base_dir, href):
    path, _, frag = unquote(href).partition("#")
    return posixpath.normpath(posixpath.join(base_dir, path)) if path else None, frag or None


def _ncx_entries(root, base_dir):
    entries = []

    def walk(parent, depth):
        for point in parent.findall("ncx:navPoint", NS):
            label = point.find("ncx:navLabel/ncx:text", NS)
            content = point.find("ncx:content", NS)
            if label is not None and content is not None and label.text:
                path, frag = _resolve(base_dir, content.get("src", ""))
                entries.append((_clean(label.text), depth, path, frag))
            walk(point, depth + 1)

    nav_map = root.find("ncx:navMap", NS)
    if nav_map is not None:
        walk(nav_map, 0)
    return entries


def _nav_entries(root, base_dir):
    toc = None
    for nav in root.iter(f"{{{NS['x']}}}nav"):
        if "toc" in (nav.get("{http://www.idpf.org/2007/ops}type") or ""):
            toc = nav
            break
    if toc is None:
        return []

    entries = []

    def walk(ol, depth):
        for li in ol.findall("x:li", NS):
            a = li.find("x:a", NS)
            if a is not None and a.get("href"):
                path, frag = _resolve(base_dir, a.get("href"))
                entries.append((_clean("".join(a.itertext())), depth, path, frag))
            sub = li.find("x:ol", NS)
            if sub is not None:
                walk(sub, depth + 1)

    ol = toc.find("x:ol", NS)
    if ol is not None:
        walk(ol, 0)
    return entries


def load(epub_path):
    """{"title", "author", "language", "cover": (bytes, ext) | None,
    "blocks": [{"type", "level", "text", "sentences"}],
    "chapters": [{"title", "depth", "block"}]}."""
    try:
        zf = zipfile.ZipFile(epub_path)
    except (OSError, zipfile.BadZipFile):
        raise EpubError("error_epub_invalido")

    with zf:
        if any(n.endswith("encryption.xml") for n in zf.namelist()):
            enc = zf.read(next(n for n in zf.namelist() if n.endswith("encryption.xml")))
            if b"EncryptedData" in enc and b"font" not in enc.lower():
                raise EpubError("error_drm")

        container = _xml(zf, "META-INF/container.xml")
        rootfile = container.find(".//c:rootfile", NS) if container is not None else None
        if rootfile is None:
            raise EpubError("error_epub_invalido")
        opf_path = rootfile.get("full-path")
        opf = _xml(zf, opf_path)
        if opf is None:
            raise EpubError("error_epub_invalido")
        base_dir = posixpath.dirname(opf_path)

        meta = opf.find("opf:metadata", NS)

        def dc(name):
            el = meta.find(f"dc:{name}", NS) if meta is not None else None
            return _clean(el.text) if el is not None and el.text else ""

        manifest = {}
        for item in opf.findall("opf:manifest/opf:item", NS):
            manifest[item.get("id")] = {
                "path": _resolve(base_dir, item.get("href", ""))[0],
                "type": item.get("media-type", ""),
                "props": item.get("properties", ""),
            }

        cover = None
        cover_id = next((m.get("content") for m in (meta.findall("opf:meta", NS) if meta is not None else [])
                         if m.get("name") == "cover"), None)
        cover_item = manifest.get(cover_id) or next(
            (m for m in manifest.values() if "cover-image" in m["props"]), None)
        if cover_item and cover_item["type"].startswith("image/"):
            try:
                cover = (zf.read(cover_item["path"]), cover_item["type"].split("/")[1].replace("jpeg", "jpg"))
            except KeyError:
                pass

        spine = opf.find("opf:spine", NS)
        toc_entries = []
        nav_item = next((m for m in manifest.values() if "nav" in m["props"].split()), None)
        if nav_item:
            root = _xml(zf, nav_item["path"])
            if root is not None:
                toc_entries = _nav_entries(root, posixpath.dirname(nav_item["path"]))
        if not toc_entries and spine is not None and spine.get("toc") in manifest:
            ncx_path = manifest[spine.get("toc")]["path"]
            root = _xml(zf, ncx_path)
            if root is not None:
                toc_entries = _ncx_entries(root, posixpath.dirname(ncx_path))

        blocks = []
        anchors = {}
        for itemref in (spine.findall("opf:itemref", NS) if spine is not None else []):
            idref = itemref.get("idref")
            item = manifest.get(idref)
            if not item or "html" not in item["type"]:
                continue
            try:
                raw = zf.read(item["path"]).decode("utf-8", errors="replace")
            except KeyError:
                continue
            parser = _BlockParser()
            parser.feed(raw)
            parser.close()
            offset = len(blocks)
            anchors[(item["path"], None)] = offset
            for anchor, idx in parser.anchors.items():
                anchors[(item["path"], anchor)] = offset + idx
            blocks.extend(parser.blocks)

    start, end = 0, len(blocks)
    edge = int(len(blocks) * PG_EDGE)
    for i in range(edge):
        if PG_HEAD_RE.search(blocks[i]["text"]):
            start = i + 1
    for i in range(len(blocks) - edge, len(blocks)):
        if PG_TAIL_RE.search(blocks[i]["text"]):
            end = i
            break
    blocks = blocks[start:end]
    if not blocks:
        raise EpubError("error_sin_texto")

    title = dc("title") or posixpath.splitext(posixpath.basename(epub_path))[0]

    chapters = []
    seen = set()
    for label, depth, path, frag in toc_entries:
        idx = anchors.get((path, frag), anchors.get((path, None)))
        if idx is None:
            continue
        idx -= start
        if idx < 0 or idx >= len(blocks) or idx in seen:
            continue
        seen.add(idx)
        chapters.append({"title": label, "depth": depth, "block": idx})
    chapters.sort(key=lambda c: c["block"])
    if not chapters or chapters[0]["block"] > 0:
        chapters.insert(0, {"title": title, "depth": 0, "block": 0})

    for block in blocks:
        block["sentences"] = [block["text"]] if block["type"] == "h" else split_sentences(block["text"])

    return {
        "title": title,
        "author": dc("creator"),
        "language": (dc("language") or "es").split("-")[0].lower(),
        "cover": cover,
        "blocks": blocks,
        "chapters": chapters,
    }
