# OCR image for Invoicerr's OCR_ENGINE=local backend: ocrmypdf (Ghostscript + Tesseract, reads PDFs
# directly) + every Tesseract language pack, behind a tiny stdlib HTTP wrapper (server.py).
FROM jbarlow83/ocrmypdf:latest

# Base image runs as non-root `app`; installing packages needs root, dropped back to `app` at the end.
USER root

# All Tesseract language packs (tesseract-ocr-all pulls every tesseract-ocr-<lang> Debian ships).
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr-all \
    && rm -rf /var/lib/apt/lists/*

# `equ` (math-formula model) isn't a Debian package — fetch it straight into tessdata.
# --chmod=644 because ADD-from-URL defaults to 0600, unreadable by the unprivileged `app` user.
ADD --chmod=644 https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/equ.traineddata \
    /usr/share/tesseract-ocr/5/tessdata/equ.traineddata

COPY --chmod=755 server.py /app/server.py
USER app

# Default -l when a request sends no ?lang=; all langs are installed, callers override per document.
ENV OCR_DEFAULT_LANGUAGES="eng+fra+deu+ita+spa+por+nld+pol+rus"
ENV PORT=9998
EXPOSE 9998
ENTRYPOINT ["python3", "/app/server.py"]
