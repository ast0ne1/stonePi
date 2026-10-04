import uvicorn

from app.config import env
from app.main import app

if __name__ == "__main__":
    # Loopback only: the Car Thing reaches it through `adb reverse`, admins through Notify's preview proxy.
    uvicorn.run(app, host=env.host, port=env.port, timeout_keep_alive=15, access_log=False)
