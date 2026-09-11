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

The base image ships `eng`/`deu`/`fra`/`spa`/`por`/`chi_sim`. This image adds
`ita nld pol rus ara chi-sim jpn` and the `equ` math model (fetched from upstream `tessdata`, as it
isn't a Debian package), for **+24.5 MB** over the base. Adding another is one line in the
`Dockerfile`. The always-on default is `eng+fra+deu+ita+spa+por+nld+pol+rus`
(`OCR_DEFAULT_LANGUAGES`); the broader non-Latin packs stay opt-in per request (`?lang=`) since every
extra language in one run slightly lowers Latin-script accuracy.

## Use it from Invoicerr

The main app's OCR service (`ROLE=ocr`, `OCR_ENGINE=local`) calls this container over HTTP via
`LOCAL_OCR_URL`. It is **opt-in**: a self-hoster who doesn't enable the `ocr-local` compose profile
gets no OCR — full-local-by-default, no key anywhere. An operator running the SaaS enables this one
container once and every tenant gets OCR.

```yaml
# invoicerr docker-compose.yml (profile: ocr-local)
ocr-local-engine:
  image: ghcr.io/invoicerr-app/ocr-image:latest
  profiles: ["ocr-local"]
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
