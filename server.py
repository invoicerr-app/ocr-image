#!/usr/bin/env python3
"""
A tiny HTTP wrapper around the `ocrmypdf` CLI (the Dockerfile in this same directory builds the
image). `ocrmypdf` is a CLI, not a service — this wraps it, deliberately Python STDLIB ONLY
(`http.server` + `subprocess` + `tempfile`): the base image is already Python (its own
`pypdfium2` rasterizer runs in-process inside `ocrmypdf` itself), so reusing that interpreter for a
two-endpoint wrapper needs no new dependency, no `pip install`, nothing for this Dockerfile to pin
or patch for CVEs later. The same "no heavyweight framework for two endpoints" reasoning this
backend's own `ocr-server.ts` (bare `node:http`, no Nest/Express) already holds one repo over.

## Endpoint contract — WE OWN BOTH SIDES (`backend/src/ocr-service/local-client.ts` is the other
half), so this is a deliberate, from-scratch design, not a Tika-compatibility shim:

  GET  /health         -> 200 JSON {"status","engine","ocrmypdfVersion","defaultLanguages",
                                     "installedLanguages"} — never the request body's contents,
                           never a secret (this server holds none), just enough for an operator's
                           own healthcheck/logs to see the container is up AND which languages it can
                           actually recognize (the whole point of replacing Tika's frozen language
                           set — see the Dockerfile's own header).
  POST /ocr[?lang=...] -> body: the raw PDF bytes (Content-Length required — no chunked-transfer
                           support, since this server's only real caller, `local-client.ts`, sends a
                           plain `Buffer` body and Node's own `fetch` always sets Content-Length for
                           one). `lang` is OPTIONAL, `+`-joined Tesseract codes (e.g. `eng+pol`) — a
                           deliberately narrow allow-list of characters, rejected with a NAMED 400
                           rather than silently substituted, the same "never guess a bad input away"
                           discipline `ocr-server.ts`'s own `resolveEngine` already holds one process
                           away for an unrecognized `OCR_ENGINE`. Returns 200 `text/plain` — the
                           recognized text, nothing else, no JSON envelope, matching the exact shape
                           `local-client.ts` already expects from ITS side of this contract (Tika
                           answered the same way; keeping it means that file's own `mapOcrTextToProposal`
                           needed zero changes for this engine swap, only the URL/verb).

## THE ocrmypdf INVOCATION — verified against a REAL running `jbarlow83/ocrmypdf:latest` container,
never guessed from `--help` text alone:

    ocrmypdf --force-ocr --output-type none --sidecar <tmp>.txt -l <languages> --quiet <in>.pdf -

  - `--force-ocr`, NOT `--skip-text`: a REAL round-trip against a
    genuine TEXT-LAYER pdf-lib-drawn PDF (no scanned image at all) proved `--skip-text`'s sidecar for
    a skipped page is the literal placeholder string `[OCR skipped on page(s) 1]` — NOT the page's
    own already-digital text. That is a real regression from Tika (PDFBox read a text layer directly,
    no OCR involved, real text back either way) for the single most common real-world case this
    engine will ever see: an inbound invoice PDF from ordinary invoicing software, genuine text, no
    embedded structured XML (`apply-ocr-fallback.ts`'s OWN trigger condition is "no structural XML
    found" — NOT "this is a scanned image" — so a plain-text, non-structured PDF is squarely this
    fallback's most ordinary customer, not an edge case). `--force-ocr` rasterizes EVERY page and
    always runs Tesseract over it, so the sidecar is populated either way — verified: a `--force-ocr`
    run against that same text-layer PDF correctly recognized every line. The honest
    cost, stated up front and never hidden: Tesseract reading a rasterized rendering of already-crisp
    vector text is very slightly lossier than PDFBox's own native text extraction was, and force-OCR
    is slower than skip-text's fast pass-through for that same page — both meaningfully smaller costs
    than silently returning a placeholder string as this fallback's "recognized text" would have been.
  - `--output-type none` + a literal `-` for `output_pdf`: this wrapper only ever wants the sidecar
    TEXT, never a rebuilt output PDF (`local-client.ts`'s own contract is "text in, text back", the
    input PDF is discarded either way) — `--output-type none` skips the (comparatively expensive)
    PDF/A re-encode step entirely. Verified: `--output-type none` REFUSES a real file path for
    `output_pdf` ("Since you specified `--output-type none`, the output file ... cannot be produced")
    and requires `-` (stdout) instead — this wrapper redirects that stdout to `DEVNULL`, never reads
    it.
  - `--quiet`: `ocrmypdf`'s own progress/INFO lines (e.g. "Parsing N pages with HocrParser") would
    otherwise land on stderr for EVERY request — this wrapper's own error path already surfaces
    stderr verbatim on a real failure, so quiet keeps a healthy container's own logs from being
    dominated by per-request noise.

## Honest limits of the wrapper itself (beyond `local-client.ts`'s own heuristic-mapping limits,
documented one file up on the Node side — this section is about the OCR STEP itself):
  - Heuristic text, not structured annotation: `ocrmypdf`/Tesseract answers "here is the text on the
    page", the same honest ceiling Tika already had — nothing here reads layout, tables, or field
    semantics the way Mistral's own `document_annotation` does (see `local-client.ts`'s header).
  - Every page of every request is rasterized and OCR'd from scratch (`--force-ocr`, see above) —
    there is no per-page cache; a multi-page PDF costs roughly linearly more wall-clock time than a
    single page, unlike PDFBox's near-instant native-text fast path for a page that needed no OCR
    at all.
  - Language coverage is real but still finite: this Dockerfile's own comment block names exactly
    which packs are installed — a document in a language NOT in that list is handed to whichever
    default (or explicitly requested) languages ARE installed and will misrecognize accordingly,
    the same "wrong language model, no error" honest gap Tika had, just with a much broader list and,
    unlike Tika, an operator's own one-line Dockerfile fix (or `?lang=` override) rather than a
    silent, uncorrectable ceiling.
"""

import json
import os
import re
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# `+`-joined Tesseract language codes only (e.g. "eng+fra+pol") — passed to `ocrmypdf` as a plain
# argv element (never through a shell), so this is not a command-injection guard so much as an
# honest-input one: a request that names something that plainly isn't a language-code list gets a
# clear 400 explaining why, rather than an opaque `ocrmypdf` failure surfacing as a generic 422.
_LANG_RE = re.compile(r'^[a-zA-Z0-9_+]{1,100}$')

DEFAULT_LANGUAGES = os.environ.get('OCR_DEFAULT_LANGUAGES', 'eng+fra+deu+ita+spa+por+nld+pol+rus')
# How long a single `ocrmypdf` invocation may run before this wrapper gives up and reports a named
# timeout — a bound INDEPENDENT of `local-client.ts`'s own client-side abort (that abort only ever
# closes the HTTP connection; without a server-side bound too, a pathological upload could keep a
# worker thread — and the `ocrmypdf` child process it spawned — running forever).
TIMEOUT_SECONDS = int(os.environ.get('OCR_TIMEOUT_SECONDS', '120'))
PORT = int(os.environ.get('PORT', '9998'))


def _list_installed_languages() -> list[str]:
    """Read once at startup (the installed set is fixed at build time — see the Dockerfile's own
    language block — so re-running this per request would only ever burn CPU for the same answer).
    Never raises: `/health` must stay a reliable "is the container up" signal even if this one,
    non-essential detail can't be read for some reason."""
    try:
        result = subprocess.run(
            ['tesseract', '--list-langs'], capture_output=True, text=True, timeout=10, check=True
        )
        # First line is a header ("List of available languages ..."); the rest, one code per line.
        return sorted(line.strip() for line in result.stdout.splitlines()[1:] if line.strip())
    except Exception:
        return []


def _ocrmypdf_version() -> str:
    try:
        result = subprocess.run(
            ['ocrmypdf', '--version'], capture_output=True, text=True, timeout=10, check=True
        )
        # Verified against a real running container: `ocrmypdf --version` prints to STDERR, not
        # stdout (an upstream quirk, not a typo here) — checking stdout first anyway keeps this
        # correct if a future `ocrmypdf` release moves it, without ever hard-depending on that.
        return (result.stdout.strip() or result.stderr.strip()) or 'unknown'
    except Exception:
        return 'unknown'


INSTALLED_LANGUAGES = _list_installed_languages()
OCRMYPDF_VERSION = _ocrmypdf_version()


class OcrRequestHandler(BaseHTTPRequestHandler):
    # Quiets `BaseHTTPRequestHandler`'s own default access-log line's HTML-escaping quirks and
    # keeps this file's log format consistent with `ocr-server.ts`'s own `[ocr] ...` prefix one
    # process away, for an operator grepping combined container logs.
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib override signature
        print(f'[ocr-local] {self.address_string()} - {fmt % args}')

    def _send_text(self, status: int, body: str) -> None:
        encoded = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        if self.path == '/health':
            self._send_json(
                200,
                {
                    'status': 'ok',
                    'engine': 'ocrmypdf',
                    'ocrmypdfVersion': OCRMYPDF_VERSION,
                    'defaultLanguages': DEFAULT_LANGUAGES,
                    'installedLanguages': INSTALLED_LANGUAGES,
                },
            )
            return
        self._send_json(404, {'error': 'not found'})

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        if urlsplit(self.path).path != '/ocr':
            self._send_json(404, {'error': 'not found'})
            return

        languages = self._resolve_languages()
        if languages is None:
            return  # `_resolve_languages` already sent the 400 for an invalid `?lang=`.

        content_length = self.headers.get('Content-Length')
        if content_length is None:
            # No chunked-transfer support — see this file's own header on why that is an
            # intentional, documented narrowing rather than an oversight.
            self._send_text(411, 'Content-Length is required.')
            return
        try:
            length = int(content_length)
        except ValueError:
            self._send_text(400, 'Content-Length must be an integer.')
            return
        if length <= 0:
            self._send_text(400, 'Request body must not be empty.')
            return

        pdf_bytes = self.rfile.read(length)
        self._run_ocr(pdf_bytes, languages)

    def _resolve_languages(self) -> str | None:
        """`None` doubles as "already answered the client" (a 400) — the caller checks for it and
        returns without doing anything further, the same short-circuit shape `readJsonBody`'s own
        callers use one repo over in `ocr-server.ts`."""
        # Deliberately NOT `urllib.parse.parse_qs` here — verified: it decodes `+` to a literal
        # space (`application/x-www-form-urlencoded` semantics), which would silently break the
        # exact `+`-joined convention `ocrmypdf -l` itself requires (`?lang=eng+fra` would arrive as
        # `"eng fra"`). `_LANG_RE`'s own narrow charset already makes percent-decoding moot — nothing
        # in a valid value is ever percent-encoded by a normal client — so a plain split is both
        # simpler AND more correct for this one, deliberately narrow parameter.
        query_pairs = dict(
            pair.split('=', 1) for pair in urlsplit(self.path).query.split('&') if '=' in pair
        )
        requested = query_pairs.get('lang')
        if requested is None:
            return DEFAULT_LANGUAGES
        if not _LANG_RE.match(requested):
            self._send_text(
                400,
                f'Invalid ?lang= value "{requested}" — expected `+`-joined Tesseract language '
                'codes (e.g. "eng+fra+pol").',
            )
            return None
        return requested

    def _run_ocr(self, pdf_bytes: bytes, languages: str) -> None:
        # A fresh temp file per request (never a shared/reused path): concurrent requests run on
        # separate threads (`ThreadingHTTPServer` below), and `ocrmypdf` needs a real path on disk —
        # it does not read a PDF from stdin the way its OWN output can be written to stdout.
        with tempfile.TemporaryDirectory(prefix='ocr-local-') as workdir:
            input_path = os.path.join(workdir, 'input.pdf')
            sidecar_path = os.path.join(workdir, 'sidecar.txt')
            with open(input_path, 'wb') as f:
                f.write(pdf_bytes)

            try:
                result = subprocess.run(
                    [
                        'ocrmypdf',
                        '--force-ocr',
                        '--output-type',
                        'none',
                        '--sidecar',
                        sidecar_path,
                        '-l',
                        languages,
                        '--quiet',
                        input_path,
                        '-',
                    ],
                    stdout=subprocess.DEVNULL,  # the discarded output PDF stream — see this file's
                    # own header on why `--output-type none` + `-` is the only combination `ocrmypdf`
                    # accepts, and why this wrapper never wants that stream anyway.
                    stderr=subprocess.PIPE,
                    timeout=TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                self._send_text(
                    504, f'ocrmypdf did not finish within {TIMEOUT_SECONDS}s (OCR_TIMEOUT_SECONDS).'
                )
                return

            if result.returncode != 0:
                # A real `ocrmypdf` failure (corrupt/encrypted/non-PDF input, an unrecognized
                # language code that slipped past `_LANG_RE`'s own shape check, ...) — the upstream
                # tool's own stderr, verbatim, truncated the same 300-char way `local-client.ts`'s
                # own `LocalOcrError` already truncates an upstream body one process away. 422
                # (Unprocessable Entity): this wrapper's OWN process is fine, the INPUT was not.
                stderr = result.stderr.decode('utf-8', errors='replace').strip()
                self._send_text(422, stderr[:300] or f'ocrmypdf exited with status {result.returncode}')
                return

            try:
                with open(sidecar_path, 'r', encoding='utf-8') as f:
                    text = f.read()
            except OSError as err:
                # Should not happen if `ocrmypdf` reported success — named rather than a bare 500,
                # per this codebase's own "never a bare Error" discipline (`ocr-server.ts`'s header).
                self._send_text(500, f'ocrmypdf reported success but the sidecar was unreadable: {err}')
                return

            self._send_text(200, text)


def main() -> None:
    server = ThreadingHTTPServer(('0.0.0.0', PORT), OcrRequestHandler)
    print(
        f'[ocr-local] ready — listening on :{PORT} '
        f'(ocrmypdf {OCRMYPDF_VERSION}, default languages "{DEFAULT_LANGUAGES}", '
        f'{len(INSTALLED_LANGUAGES)} installed: {",".join(INSTALLED_LANGUAGES)})'
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == '__main__':
    main()
