# Videos

Short vertical (9:16) MP4 clips shown on the website.

## Add a video

1. Put the MP4 file in this folder, e.g. `remodel-before-after.mp4`. Keep it under ~20 MB.
2. Add a line to `videos.json` under the right page:

```json
{
  "construction": [
    { "file": "remodel-before-after.mp4", "caption": "Same room. 2 weeks later." }
  ],
  "sell": [
    { "file": "cash-offer-intro.mp4", "caption": "Fair cash offer, no repairs needed." }
  ]
}
```

- `construction` shows on the home page (Hands of Faith Construction).
- `sell` shows on `/sell/`.
- A page's video section stays hidden while its list is empty.
- Optional: `"poster": "image.jpg"` for a thumbnail.
