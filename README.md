# Smart Traffic Signal System (SignalSync)

> **Safety disclaimer:** academic/simulation prototype. It is **not** connected to
> real traffic lights and must **not** be used on public roads. Extensive safety
> validation and certified traffic-control hardware are required before any
> real-world deployment.

Adaptive traffic-signal control using computer vision and traffic-density
estimation. Built phase by phase; this README is expanded in Phase 17.

## Status
| Phase | Description | State |
|---|---|---|
| 1 | Project setup, config, FastAPI skeleton | done |
| 2 | Video input (file / webcam / IP camera) | done |
| 3-17 | Detection, tracking, density, controllers, API, DB, dashboard, experiments | pending |

## Quick start (Python 3.11+)
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # Windows: copy .env.example .env
python scripts/generate_sample_video.py              # synthetic clip for smoke tests
python scripts/phase2_demo.py --source sample_data/synthetic_traffic.mp4
python run.py                                        # http://localhost:8000/docs
pytest
```
Use real footage by placing it in `sample_data/` and setting `VIDEO_SOURCE` in `.env`
(path, webcam index such as `0`, or `rtsp://` / `http://` URL).
