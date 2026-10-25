import uvicorn

from app.config import env
from app.main import app

# proxy_headers off: uvicorn would otherwise replace the loopback peer (nginx, Funnel) with
# the address in X-Forwarded-For, and AgentLink would stop trusting nginx's X-StonePi-Path
# (pebble.from_proxy) and treat every LAN device as away. AgentLink reads the client IP from
# X-Real-IP itself, and only when the peer is loopback.
OPTIONS = {"timeout_keep_alive": 15, "access_log": False, "proxy_headers": False}

if __name__ == "__main__":
    # Loopback on the Pi (nginx and Funnel in front). run-dev binds all interfaces so a Pebble
    # on the LAN can reach the dev PC.
    uvicorn.run(app, host=env.host, port=env.port, **OPTIONS)
