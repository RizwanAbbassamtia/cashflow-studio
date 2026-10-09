"""Export stage helpers (docs/M3-M4-CONTRACT.md section 4).

* ``thumbnail.py``: the thumbnail template read from the competitor thumbnail (cached per
  video in the channel folder), the text-free subject image, Pillow composition of three
  headline variants in 16:9 and 9:16, and the pHash similarity gate.
* ``seo.py``: the SEO pack (title, description, tags, chapters, pinned comment, hashtags)
  in the target language, chapters from the script sections and ``timing.json``.
* ``provenance.py``: ``provenance.json`` / ``provenance.md`` and the disclosure record.
* ``exporter.py``: the export folder layout and the file copies.
* ``_phash.py``: a pure-Pillow perceptual hash used when ``images/phash.py`` is not there.
"""
