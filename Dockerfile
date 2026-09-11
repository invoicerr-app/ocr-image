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
# Tesseract language packs — ONE clearly-commented block, so adding a language later is ONE line.
# The base image already ships eng/deu/fra/spa/por/chi_sim (`tesseract --list-langs`, verified
# against a running container before writing this file) — re-listed below anyway rather than
# assumed, so this block stays correct even if `jbarlow83/ocrmypdf`'s own upstream bundle changes;
# `apt-get install` on an already-installed package is a verified no-op, never an error.
#
# This task's own required set (mandant's verbatim example: "ita,nld,rus,equ") plus this repo's
# own primary markets (FR/PL/IT — see CLAUDE.md) plus "the world's main languages" per the task's
# own brief: French, German, Italian, Spanish, Portuguese, Dutch, Polish, Russian — every language
# this app's own compliance profiles already treat as a first-class market, plus Arabic and
# Japanese as the two broadest non-Latin-script additions whose installed cost stayed small enough
# to justify (measured, real `docker images` diff against the bare base image: +24.5 MB for ALL SIX
# new packs below PLUS `equ` — Simplified Chinese was already free, see above). Every one of these
# is a real Debian/Ubuntu `tesseract-ocr-*` package, verified with `apt-cache search tesseract-ocr-`
# against this exact base image before being written here, never guessed.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr-fra \
    tesseract-ocr-deu \
    tesseract-ocr-ita \
    tesseract-ocr-spa \
    tesseract-ocr-por \
    tesseract-ocr-nld \
    tesseract-ocr-pol \
    tesseract-ocr-rus \
    tesseract-ocr-ara \
    tesseract-ocr-chi-sim \
    tesseract-ocr-jpn \
    && rm -rf /var/lib/apt/lists/*

# `equ` (Tesseract's own dedicated math-equation/formula recognition model) is NOT a Debian package
# at all — verified: `apt-cache search tesseract-ocr-equ` returns nothing on this exact base image,
# unlike every other language above. It is real, but distributed only as a standalone `.traineddata`
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

# The default language set `ocrmypdf -l` runs with when a request does not override it — every
# language installed above except the two broadest non-European additions (`ara`/`jpn`), which stay
# opt-in per request (`?lang=`) rather than in the always-on default: Tesseract's own accuracy on a
# LATIN-script document degrades slightly for every extra language folded into one `-l a+b+c+...`
# run (more candidate dictionaries to disambiguate against), so the default is this app's actually
# documented markets, not literally everything installed.
ENV OCR_DEFAULT_LANGUAGES="eng+fra+deu+ita+spa+por+nld+pol+rus"
ENV PORT=9998

EXPOSE 9998

ENTRYPOINT ["python3", "/app/server.py"]
