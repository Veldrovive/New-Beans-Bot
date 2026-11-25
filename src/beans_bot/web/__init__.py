"""
The admin website's API. FastAPI, served by Hypercorn inside the bot process (see cogs/web.py).

- It is treated like a cog: it calls services and never opens a DB session itself or imports a cog.
- Anything only a cog can do (post an automatic bet, retrain the classifier) is a hook the cog
  registers on its service.
- There is no login. Cloudflare Access sits in front, the port is only published on localhost, and
  every non-GET request must carry `X-Beans-Admin: 1` so other websites can't trigger actions.
"""
