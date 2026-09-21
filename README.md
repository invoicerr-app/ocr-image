# ocr-image

Self-hosted OCR microservice image for [Invoicerr](https://github.com/invoicerr-app/invoicerr) —
**100 % local, no cloud key**, covering the world's main languages.

A tiny HTTP service on top of [`jbarlow83/ocrmypdf`](https://github.com/ocrmypdf/OCRmyPDF)
(Ghostscript + Tesseract, real PDF → OCR). You send it a PDF; it runs `ocrmypdf --sidecar` and
returns the recognised **plain text**. No API key, nothing ever leaves the machine.

## Endpoint contract

| Method | Path | In | Out |
|---|---|---|---|
| `GET` | `/health` | — | `200` JSON: `status`, `engine`, `ocrmypdfVersion`, `defaultLanguages`, installed languages |
| `POST` | `/ocr[?lang=fra+deu]` | raw PDF bytes (`Content-Length` required) | `200 text/plain` — the OCR text |

It runs, verbatim:

```
ocrmypdf --force-ocr --output-type none --sidecar <tmp>.txt -l <languages> --quiet <in>.pdf -
```

`--force-ocr` (not `--skip-text`) is deliberate: a real round-trip showed `--skip-text` returns the
literal placeholder `[OCR skipped on page(s) 1]` for a page that already carries a text layer, which
would be a silent regression for Invoicerr's fallback (it OCRs whenever no *structured* data was
found, so an ordinary digital-text PDF is the common case).

## Languages

**Every Tesseract language Debian ships is installed** — via the `tesseract-ocr-all` meta-package
(<https://tesseractocr.org> puts the project itself at "100+ languages"; the exact set is whatever
Debian/Ubuntu resolves `tesseract-ocr-all` to at build time, not something this Dockerfile pins) —
plus script-only models (`Cyrillic`, `Devanagari`, …) and the `equ` math model on top (fetched from
upstream `tessdata`, as it isn't a Debian package). Installing everything grows the image
substantially (the full standard `tessdata` set); a real build on 2026-09-21 landed 162 entries in
`tesseract --list-langs` / `/health`'s `installedLanguages` — that endpoint, not this paragraph, is
the current, authoritative count.

The per-run default (`OCR_DEFAULT_LANGUAGES`) is a sensible Latin-script subset
(`eng+fra+deu+ita+spa+por+nld+pol+rus`), **not** all of them at once — Tesseract's accuracy drops
slightly for each extra language in a single run. Pass the document's real language(s) with `?lang=`
(any installed code) for best results.

## Use it from Invoicerr

The main app's local OCR provider calls this container over HTTP via `OCR_SERVICE_URL` (e.g.
`http://ocr:9998`). The variable is unset by default, and unset is not a failure: the app treats "no
OCR engine configured" as the honest, self-hosted-by-default outcome, not an error. It is **opt-in**:
a self-hoster who doesn't enable the `ocr` compose profile gets no OCR — full-local-by-default, no key
anywhere. An operator running the SaaS enables this one container once and every tenant gets OCR.

```yaml
# invoicerr docker-compose.yml (profile: ocr)
ocr:
  image: ghcr.io/invoicerr-app/ocr-image:latest
  profiles: ["ocr"]
```

```bash
docker pull ghcr.io/invoicerr-app/ocr-image:latest
```

## Honest limits

Heuristic text extraction, not structured annotation like a cloud model; `--force-ocr` is slightly
slower/lossier than a native-text fast path on already-digital pages; language coverage is broad but
finite (one-line fix to extend).

## License

AGPL-3.0 (same as the main project — see `LICENSE`).
