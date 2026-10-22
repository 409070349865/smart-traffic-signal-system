"""Start the Smart Traffic Signal System:  python run.py"""
import uvicorn

from app.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run("app.main:app", host=s.app.host, port=s.app.port,
                log_level=s.app.log_level.lower())


if __name__ == "__main__":
    main()
