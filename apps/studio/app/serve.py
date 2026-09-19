import uvicorn

from app.config import env
from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host=env.host, port=env.port)
