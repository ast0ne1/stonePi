from pathlib import Path

from stonepi_auth.brand import asset_rev

__version__ = "0.1.8"
__asset_rev__ = asset_rev(Path(__file__).resolve().parent / "static")  # cache-bust token; changes with static/
__github_user__ = "ast0ne1"
__github__ = "https://github.com/ast0ne1"
