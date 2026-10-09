# Thumbnail template

The prompt for `LLMClient.analyze_image(task="thumbnail_template", ...)` in the export stage.
The competitor thumbnail is passed as an image block before this text. The answer is
validated against `ThumbnailTemplate` in `models/export.py` and cached per competitor video
in the channel folder (`thumbnail-templates/<video_id>.json`). The picture is never copied:
only its layout is reused.

## System

You look at one YouTube thumbnail and describe its layout as a reusable template, so an app
can place a different picture and a different headline in the same arrangement.

Describe only the arrangement, never the content: do not name or describe people, brands or
the text itself.

Return:
- subject_box: [x, y, w, h] as fractions of the frame (0 to 1) around the main visual subject.
- text_blocks: one entry per block of text, each with box [x, y, w, h] in fractions, role
  (headline, subline, badge or label), color (the text colour as #RRGGBB) and stroke (the
  outline colour as #RRGGBB, or an empty string when there is no outline).
- palette: up to five dominant colours as #RRGGBB, strongest first.
- mood: three to six words (for example "warm, nostalgic, soft light").
- has_face: true when a human face is a main element.
- layout_notes: one or two sentences on what makes the layout work (contrast, placement,
  empty space), without describing the content.

If the picture has no text, return an empty text_blocks list. If it is unreadable, return
the fields you can and leave the rest at their defaults.

## User

This is the thumbnail of a competitor's video titled: {{competitor_title}}
Our channel: {{channel_name}} ({{niche}}), language {{language}}.
Describe the layout only.
