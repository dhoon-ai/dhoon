"""Questions about PDFs, Word and text files: search and page-by-page reading."""

import hashlib
import json
import re
from pathlib import Path

import httpx
import toolbox_server as tb
from bs4 import BeautifulSoup

# ------------------------------------------------------------------ documents ---
DOC_TYPES = {".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".html", ".htm", ".json"}
CHUNK_CHARS, CHUNK_OVERLAP = 1200, 200


def _doc_pages(p: Path) -> list[tuple[int, str]]:
    """Return [(page_number, text)] for a document. Non-PDFs get ~3000-char 'pages'."""
    suffix = p.suffix.lower()
    if suffix not in DOC_TYPES:
        raise ValueError(f"Unsupported file type {suffix}. Supported: {', '.join(sorted(DOC_TYPES))}")
    if suffix == ".pdf":
        from pypdf import PdfReader

        return [(i + 1, page.extract_text() or "") for i, page in enumerate(PdfReader(str(p)).pages)]
    if suffix == ".docx":
        import docx

        text = "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
    elif suffix == ".xlsx":  # one "page" per sheet, rows as tab-separated text
        from openpyxl import load_workbook

        wb = load_workbook(str(p), read_only=True, data_only=True)
        pages = []
        for i, ws in enumerate(wb.worksheets):
            rows = ("\t".join("" if v is None else str(v) for v in row) for row in ws.iter_rows(values_only=True))
            pages.append((i + 1, f"Sheet: {ws.title}\n" + "\n".join(r for r in rows if r.strip())))
        wb.close()
        return pages
    elif suffix in {".html", ".htm"}:
        text = BeautifulSoup(p.read_text(errors="replace"), "html.parser").get_text("\n")
    else:
        text = p.read_text(errors="replace")
    return [(i // 3000 + 1, text[i : i + 3000]) for i in range(0, max(len(text), 1), 3000)]


def _chunks(pages: list[tuple[int, str]]) -> list[dict]:
    out = []
    for page, text in pages:
        text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()
        step = CHUNK_CHARS - CHUNK_OVERLAP
        for i in range(0, len(text), step):
            piece = text[i : i + CHUNK_CHARS].strip()
            if len(piece) > 40:
                out.append({"page": page, "text": piece})
    return out


def _embed(texts: list[str]) -> list[list[float]]:
    vectors = []
    for i in range(0, len(texts), 32):
        r = httpx.post(
            f"{tb.OLLAMA_URL}/api/embed", json={"model": tb.EMBED_MODEL, "input": texts[i : i + 32]}, timeout=300
        )
        r.raise_for_status()
        vectors.extend(r.json()["embeddings"])
    return vectors


def _index(p: Path) -> list[dict]:
    """Chunk + embed a document once; cache the result in WORKSPACE/.index/."""
    st = p.stat()
    key = hashlib.sha1(f"{p}|{st.st_size}|{st.st_mtime_ns}|{tb.EMBED_MODEL}".encode()).hexdigest()
    cache = tb.WORKSPACE / ".index" / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    chunks = _chunks(_doc_pages(p))
    if not chunks:
        raise ValueError("No text found. It may be a scanned PDF (images only), which is not supported.")
    for c, v in zip(chunks, _embed(["search_document: " + c["text"] for c in chunks]), strict=True):
        c["vec"] = v
    cache.parent.mkdir(exist_ok=True)
    cache.write_text(json.dumps(chunks))
    return chunks


def _words(text: str) -> list[str]:
    return re.findall(r"[\w$%.-]+", text.lower())


def _hybrid_rank(chunks: list[dict], query: str, meaning: "list[float]") -> list[int]:
    """Combine meaning search (embeddings) with keyword search (BM25, rank-bm25), so exact
    names, numbers and terms are found too. Reciprocal rank fusion: a passage near the top
    of either list comes out near the top."""
    import numpy as np

    by_meaning = list(np.argsort(-np.asarray(meaning)))
    try:
        from rank_bm25 import BM25Okapi

        keyword = BM25Okapi([_words(c["text"]) for c in chunks]).get_scores(_words(query))
        by_keyword = [i for i in np.argsort(-keyword) if keyword[i] > 0]  # only passages with the words
        has_keywords = bool(by_keyword)
    except Exception:  # noqa: BLE001 - without the keyword part, meaning search alone
        by_keyword, has_keywords = [], False
    fused = {}
    for ranking in (by_meaning, by_keyword) if has_keywords else (by_meaning,):
        for rank, i in enumerate(ranking):
            fused[int(i)] = fused.get(int(i), 0.0) + 1 / (60 + rank)
    return sorted(fused, key=lambda i: -fused[i])


@tb.mcp.tool()
def search_document(path: str, query: str, top_k: int = 5) -> str:
    """Find the passages in a document (PDF, Word, Excel, text, Markdown, HTML, CSV) in the workspace
    that best answer a question. Use this to answer questions about a document or book.
    Quote the returned passages and cite their page numbers. The first search of a large
    document takes a minute or two while it is indexed; later searches are fast.
    Use list_files to see which documents exist."""
    import numpy as np

    p = tb._safe_path(path)
    if not p.is_file():
        return f"File not found: {path}. Use list_files to see what is in the workspace."
    try:
        chunks = _index(p)
        q = np.array(_embed(["search_query: " + query])[0])
    except (ValueError, httpx.HTTPError) as e:
        return f"Could not search {path}: {e}"
    m = np.array([c["vec"] for c in chunks])
    scores = m @ q / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-9)
    best = _hybrid_rank(chunks, query, scores)[: max(1, min(top_k, 10))]
    parts = [f"[page {chunks[i]['page']}, relevance {scores[i]:.2f}]\n{chunks[i]['text']}" for i in best]
    return (f"Top passages from {path} for: {query}\n\n" + "\n\n---\n\n".join(parts))[: tb.MAX_TEXT]


@tb.mcp.tool()
def read_document(path: str, start_page: int = 1, max_pages: int = 3) -> str:
    """Read the text of a document (PDF, Word, Excel, text...) page by page, e.g. to summarise
    the start of a book or one chapter. Shows the total page count. For questions about a
    long document, prefer search_document, which finds the relevant passages."""
    p = tb._safe_path(path)
    if not p.is_file():
        return f"File not found: {path}. Use list_files to see what is in the workspace."
    try:
        pages = _doc_pages(p)
    except ValueError as e:
        return str(e)
    start = max(1, start_page)
    chosen = [(n, t) for n, t in pages if start <= n < start + max(1, min(max_pages, 10))]
    body = "\n\n".join(f"--- page {n} ---\n{t.strip()}" for n, t in chosen) or "(no text on these pages)"
    return f"{path}: {len(pages)} pages in total.\n\n{body}"[: tb.MAX_TEXT]
