# MANDANT DECISION (verbatim, mid-task): "pour l'OCR on peut faire notre propre image et notre
# propre serveur: FROM jbarlow83/ocrmypdf:latest + RUN apt-get install tesseract-ocr-{ita,nld,rus,
# equ}…" — this file. It REPLACES `apache/tika:latest-full` as the engine behind `OCR_ENGINE=local`
# (`backend/src/ocr-service/local-client.ts`'s own header carries the full Tika-vs-this comparison
# and the honest trade-offs of switching). The vision behind the switch: a FULL-LOCAL,
# self-hostable OCR server covering the world's main languages, never tied to one image's frozen
# language set the way Tika's own `apache/tika:latest-full` was (see that client's header: Polish
# and Dutch were silently mis-recognized on the stock Tika image, with no runtime fix short of
# building a custom image — this file below IS that custom image, done properly, upstream of the
# problem instead of downstream of it).
#
# `jbarlow83/ocrmypdf` (the tool author's own official image, actively published, MIT-licensed
# `ocrmypdf` itself) bundles Ghostscript + Tesseract + pypdfium2 and — critically, the exact reason
# a bare `tesseract-ocr`/`hertzg/tesseract-server` image was rejected during the Tika evaluation
# (see `local-client.ts`'s header, candidate 1) — it READS A PDF DIRECTLY: `ocrmypdf` rasterizes
# each page itself before handing it to Tesseract, so this Dockerfile never needs a PDF-to-image
# conversion step of its own, in Python or otherwise.
FROM jbarlow83/ocrmypdf:latest

# The base image drops to a non-root `app` user (uid 1000) for its own `ocrmypdf` CLI entrypoint —
# installing packages needs root back, restored to `app` again at the very end of this file (the
# HTTP wrapper never needs to run as root, and running a network-facing process as root with no
# reason to would be a needless privilege-escalation surface).
USER root

# ---------------------------------------------------------------------------------------------
# Tesseract language packs — ALL of them. MANDANT DECISION (verbatim): "y'a 94 languages à supporter
# https://tesseractocr.org/fr/#languages". Rather than a curated subset, install the
# `tesseract-ocr-all` meta-package, which depends on every `tesseract-ocr-<lang>` pack Debian ships
# — verified on THIS exact base image (`apt-cache show tesseract-ocr-all` resolves; `apt-cache search
# '^tesseract-ocr-'` lists 162 packages: the ~94 languages plus script variants). The cost is real:
# the full standard `tessdata` set is installed, so this image is MUCH larger than the bare base (the
# CI build in this repo publishes it and its `/health` smoke test lists exactly what landed). That
# cost is the explicit requirement — "support every language". Which subset a given OCR run actually
# uses is still chosen PER REQUEST (see `OCR_DEFAULT_LANGUAGES` below), never "all ~94 at once", so
# installing everything costs image size, not per-request accuracy.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr-all \
    && rm -rf /var/lib/apt/lists/*

# `equ` (Tesseract's own dedicated math-equation/formula recognition model) is NOT a Debian package
# at all — verified: `apt-cache search tesseract-ocr-equ` returns nothing on this exact base image,
# so even `tesseract-ocr-all` above does NOT include it. It is real, but distributed only as a `.traineddata`
# file in the upstream `tesseract-ocr/tessdata` project (the same "standard" tessdata tier Ubuntu's
# own `tesseract-ocr-*` packages above already use — not the newer, separately-tiered
# `tessdata_best`/`tessdata_fast` repos, which would mix OCR quality tiers on the same install for no
# reason). Fetched directly into Tesseract's own data directory with a plain `ADD` from a URL —
# Docker's own built-in remote-fetch, not a new `curl`/`wget` binary in the image for a single file —
# `--chmod=644` because `ADD` from a URL defaults to `0600`, which `tesseract` (running as the
# unprivileged `app` user again by the end of this file) would not be able to read at all.
ADD --chmod=644 https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/equ.traineddata \
    /usr/share/tesseract-ocr/5/tessdata/equ.traineddata

# The HTTP wrapper itself — see that file's own header for the endpoint contract. Deliberately a
# single stdlib-only script, no `pip install` of any kind: the base image's own Python (used
# internally by `ocrmypdf` for its `pypdfium2` rasterizer) is reused as-is.
COPY --chmod=755 server.py /app/server.py

USER app

# ALL ~94 languages are INSTALLED (tesseract-ocr-all above); this is just the default set
# `ocrmypdf -l` runs with when a request doesn't override it — a sensible Latin-script subset, NOT
# "everything at once". Tesseract's accuracy on a given document degrades slightly for every extra
# language folded into one `-l a+b+c+...` run (more candidate dictionaries to disambiguate against),
# so a caller gets best results by passing the document's actual language via `?lang=` (any of the
# ~94 installed); this default just covers the app's primary markets for the common unspecified case.
ENV OCR_DEFAULT_LANGUAGES="eng+fra+deu+ita+spa+por+nld+pol+rus"
ENV PORT=9998

EXPOSE 9998

ENTRYPOINT ["python3", "/app/server.py"]
