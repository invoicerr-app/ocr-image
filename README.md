# ocr-image

Self-hosted OCR microservice image for [Invoicerr](https://github.com/invoicerr-app/invoicerr) —
**100 % local, no cloud key**, supporting the world's main languages.

It is a small HTTP service built on [`jbarlow83/ocrmypdf`](https://github.com/ocrmypdf/OCRmyPDF)
(Ghostscript + Tesseract, real PDF → OCR) with added Tesseract language packs. You send it a PDF,
it runs `ocrmypdf --sidecar` and returns the recognised **plain text**.

## Role in Invoicerr

The main app's OCR service (`ROLE=ocr`, `OCR_ENGINE=local`) calls this container over HTTP via
`LOCAL_OCR_URL` to read uploaded supplier invoices. It is **opt-in**: a self-hoster who doesn't
enable the `ocr-local` compose profile simply gets no OCR — nothing ever leaves the machine, and no
API key exists anywhere. An operator running the SaaS enables this one container once, and every
tenant gets OCR.

Published to `ghcr.io/invoicerr-app/ocr-image`. The main repo's `docker-compose.yml` (`ocr-local`
profile) points at it.

## Status

Scaffolding — the Dockerfile, the HTTP wrapper, the language-pack list and the publish workflow land
here once validated by a real OCR round-trip.
