"""Búsqueda de texto dentro de un audiolibro ya convertido, sin GTK.

book.json guarda cada frase con su tiempo (bloques `s`: [frase, inicio,
fin]) y los capítulos con su tiempo de inicio. Este módulo arma una vez
un índice normalizado -- minúsculas, sin acentos y con la puntuación
convertida en espacio -- que conserva a qué frase y a qué carácter del
texto original pertenece cada carácter. Así una cita se puede buscar tal
cual, aunque cruce un punto o un salto de párrafo, y cada coincidencia
devuelve el tiempo del audio y el capítulo donde aparece.

`segments` de cada resultado viene listo para pintar en un Gtk.Label:
pares (texto, es_coincidencia) con el contexto ya recortado.
"""

import bisect
import re
import unicodedata
from array import array

CONTEXT = 70
MAX_EXCERPT = 480
MIDDLE_SENTENCES = 2


def _fold_char(ch):
    """Carácter -> uno o más caracteres normalizados ('ñ' -> 'n',
    'ß' -> 'ss', cualquier signo -> ' ')."""
    out = []
    for part in unicodedata.normalize("NFKD", ch):
        if unicodedata.combining(part):
            continue
        if part.isalnum():
            out.append(part.casefold())
        else:
            out.append(" ")
    return "".join(out)


def normalize(text):
    """Texto comparable: minúsculas, sin acentos, puntuación como espacio
    y espacios colapsados."""
    return re.sub(r"\s+", " ", "".join(_fold_char(ch) for ch in text)).strip()


def _normalize_with_map(text):
    """Igual que normalize(), pero devuelve por cada carácter de salida el
    índice del carácter original que lo produjo."""
    chars = []
    mapping = array("i")
    for raw_index, ch in enumerate(text):
        for folded in _fold_char(ch):
            if folded == " ":
                if not chars or chars[-1] == " ":
                    continue
            chars.append(folded)
            mapping.append(raw_index)
    while chars and chars[-1] == " ":
        chars.pop()
        mapping.pop()
    return "".join(chars), mapping


def _context_start(text, position):
    start = max(0, position - CONTEXT)
    if start:
        space = text.find(" ", start, position)
        start = space + 1 if space != -1 else position
    return start


def _context_end(text, position):
    end = min(len(text), position + CONTEXT)
    if end < len(text):
        space = text.rfind(" ", position, end)
        end = space if space != -1 else position
    return end


def _same_sentence(text, start, end):
    start_ctx = _context_start(text, start)
    end_ctx = _context_end(text, end)
    segments = []
    if start_ctx > 0:
        segments.append(("…", False))
    segments.append((text[start_ctx:start], False))
    segments.append((text[start:end], True))
    segments.append((text[end:end_ctx], False))
    if end_ctx < len(text):
        segments.append(("…", False))
    return segments


def _spans_sentences(sentences, first, last, start_raw, end_raw):
    first_text = sentences[first][0]
    last_text = sentences[last][0]
    start_ctx = _context_start(first_text, start_raw)
    end_ctx = _context_end(last_text, min(end_raw, len(last_text)))

    segments = []
    if start_ctx > 0:
        segments.append(("…", False))
    segments.append((first_text[start_ctx:start_raw], False))
    segments.append((first_text[start_raw:], True))

    segments.append((" ", False))
    middle = list(range(first + 1, last))
    shown = middle[:MIDDLE_SENTENCES]
    for index, sentence in enumerate(shown):
        if index:
            segments.append((" ", False))
        segments.append((sentences[sentence][0], True))
    if len(middle) > len(shown):
        segments.append((" … ", False))
    elif shown:
        segments.append((" ", False))

    segments.append((last_text[:end_raw], True))
    segments.append((last_text[end_raw:end_ctx], False))
    if end_ctx < len(last_text):
        segments.append(("…", False))
    return segments


def _cap(segments, limit=MAX_EXCERPT):
    out = []
    used = 0
    for text, match in segments:
        remaining = limit - used
        if remaining <= 0:
            out.append(("…", False))
            break
        if len(text) > remaining:
            out.append((text[:remaining], match))
            out.append(("…", False))
            break
        out.append((text, match))
        used += len(text)
    return out or [("", False)]


class SearchIndex:
    """Índice de un book.json. Se arma en un hilo (texto grande) y se
    consulta con search()."""

    def __init__(self, book):
        self.sentences = []
        self.chapters = book.get("chapters") or []
        self.chapter_starts = [c["start"] for c in self.chapters]
        parts = []
        self.owner = array("i")
        self.rawpos = array("i")

        for block in book.get("blocks") or []:
            for raw, start, end in block.get("s") or []:
                index = len(self.sentences)
                self.sentences.append((raw, start, end))
                normalized, mapping = _normalize_with_map(raw)
                if not normalized:
                    continue
                if self.owner:
                    parts.append(" ")
                    self.owner.append(index)
                    self.rawpos.append(len(raw))
                parts.append(normalized)
                self.owner.extend([index] * len(normalized))
                self.rawpos.extend(mapping)
        self.text = "".join(parts)

    def chapter_for(self, when):
        if not self.chapter_starts:
            return ""
        index = bisect.bisect_right(self.chapter_starts, when + 0.001) - 1
        return self.chapters[index]["title"] if index >= 0 else ""

    def search(self, query, limit=60):
        """Devuelve (resultados, total). El total cuenta todas las
        coincidencias aunque solo se devuelvan `limit`."""
        needle = normalize(query)
        if not needle:
            return [], 0
        results = []
        total = 0
        position = 0
        while True:
            start = self.text.find(needle, position)
            if start < 0:
                break
            total += 1
            if len(results) < limit:
                results.append(self._result(start, start + len(needle)))
            position = start + len(needle)
        return results, total

    def _result(self, start, end):
        first = self.owner[start]
        last = self.owner[end - 1]
        start_raw = self.rawpos[start]
        end_raw = self.rawpos[end - 1] + 1
        if first == last:
            segments = _same_sentence(self.sentences[first][0], start_raw,
                                      min(end_raw, len(self.sentences[first][0])))
        else:
            segments = _spans_sentences(self.sentences, first, last, start_raw, end_raw)
        when = self.sentences[first][1]
        return {
            "sentence": first,
            "start": when,
            "end": self.sentences[last][2],
            "chapter": self.chapter_for(when),
            "segments": _cap(segments),
        }
