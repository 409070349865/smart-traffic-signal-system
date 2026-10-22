# Sample data

Place recorded traffic videos here (`.mp4`, `.avi`, `.mov`, `.mkv`) and point
`VIDEO_SOURCE` in `.env` at the file, e.g. `VIDEO_SOURCE=sample_data/traffic.mp4`.

No real traffic footage is bundled. To get a synthetic clip for smoke-testing:

    python scripts/generate_sample_video.py

This writes `sample_data/synthetic_traffic.mp4` (moving rectangles on a plain
junction). It is only for verifying the pipeline, not for evaluating results.
Free real footage can be found on sites such as Pexels / Pixabay (check licence).
