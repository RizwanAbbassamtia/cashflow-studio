"""Helpers behind the images stage (``pipeline/stages/images.py``).

* ``phash.py``: 64-bit perceptual hash and Hamming distance, pure Pillow (no numpy).
* ``hash_store.py``: the ``image_hashes`` SQLite table (dedupe against a channel's past pictures).
* ``style_sheet.py``: the channel's reference picture as ``06_images/style_sheet.png``.
* ``qa.py``: the vision check prompt and verdict text.
* ``config.py``: ``config/images.yaml``.

Submodules are imported on purpose, never from here, so importing the package costs nothing.
"""
