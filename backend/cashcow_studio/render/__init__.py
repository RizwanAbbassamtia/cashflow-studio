"""The render engine behind the edit stage: timeline building, captions, popups, music and
the FFmpeg filter graph. See docs/M3-M4-CONTRACT.md section 3.

Nothing here talks to the network or needs a GPU; FFmpeg does every frame on the CPU.
"""
