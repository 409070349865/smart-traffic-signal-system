"""Start the Smart Traffic Signal System:  python run.py"""
import uvicorn

from app.config import get_settings


def main() -> None:
    s = get_settings()
    host = s.app.host if s.app.host != "0.0.0.0" else "127.0.0.1"
    print(f"\n[SignalSync] Dashboard  --> http://{host}:{s.app.port}/")
    print(f"[SignalSync] WebSocket  --> ws://{host}:{s.app.port}/ws/traffic")
    print(f"[SignalSync] API Docs   --> http://{host}:{s.app.port}/docs\n")
    uvicorn.run(
        "app.main:app",
        host=s.app.host,
        port=s.app.port,
        log_level=s.app.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
