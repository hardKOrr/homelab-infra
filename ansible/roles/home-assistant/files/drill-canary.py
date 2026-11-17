# Home Assistant drill canary client, run by tasks/drill-canary.yml inside Home Assistant's
# own Python (it ships aiohttp). Helpers and core settings are WebSocket-only, so this
# speaks the WebSocket API on 127.0.0.1:8123.
#
# stdin: {"action": "create|fresh|verify", "final": bool, "canary": str,
#         "username": str, "password": str}
# stdout: one JSON line {"onboarded": bool, "helpers": [names], "location": str,
#         "created_owner": bool}, or {"error": str}. It always exits 0, because the
#         caller hides this command's result (stdin holds the password) and reports the
#         error itself; no error text carries the password.
import asyncio
import json
import sys

import aiohttp

BASE = "http://127.0.0.1:8123"
CLIENT = BASE + "/"
DEFAULT_LOCATION = "Home"


async def token_from_code(http, code):
    async with http.post(BASE + "/auth/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": CLIENT}) as r:
        r.raise_for_status()
        return (await r.json())["access_token"]


async def sign_in(http, req):
    async with http.post(BASE + "/auth/login_flow", json={
            "client_id": CLIENT, "handler": ["homeassistant", None],
            "redirect_uri": CLIENT}) as r:
        r.raise_for_status()
        flow = await r.json()
    async with http.post(BASE + "/auth/login_flow/" + flow["flow_id"], json={
            "client_id": CLIENT, "username": req["username"],
            "password": req["password"]}) as r:
        r.raise_for_status()
        step = await r.json()
    if step.get("type") != "create_entry":
        sys.exit("Home Assistant refused the drill owner's login: %s" % step.get("errors"))
    return await token_from_code(http, step["result"])


async def main():
    req = json.loads(sys.stdin.read())
    out = {"onboarded": True, "helpers": [], "location": "", "created_owner": False}
    async with aiohttp.ClientSession() as http:
        async with http.get(BASE + "/api/onboarding") as r:
            steps = await r.json() if r.status == 200 else []
        user_done = all(s.get("done") for s in steps if s.get("step") == "user")
        if not user_done:
            out["onboarded"] = False
            if req["action"] != "create":
                print(json.dumps(out))
                return
            async with http.post(BASE + "/api/onboarding/users", json={
                    "client_id": CLIENT, "name": "Drill Owner", "language": "en",
                    "username": req["username"], "password": req["password"]}) as r:
                r.raise_for_status()
                token = await token_from_code(http, (await r.json())["auth_code"])
            out["onboarded"] = out["created_owner"] = True
        else:
            token = await sign_in(http, req)

        async with http.ws_connect(BASE + "/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            if (await ws.receive_json()).get("type") != "auth_ok":
                sys.exit("Home Assistant refused the WebSocket session.")
            seq = 0

            async def call(msg):
                nonlocal seq
                seq += 1
                await ws.send_json(dict(msg, id=seq))
                while True:
                    reply = await ws.receive_json()
                    if reply.get("id") == seq and reply.get("type") == "result":
                        if not reply.get("success"):
                            sys.exit("Home Assistant refused %s: %s" % (msg["type"], reply.get("error")))
                        return reply.get("result")

            helpers = await call({"type": "input_boolean/list"})
            mine = [h for h in helpers if h.get("name") == req["canary"]]
            if req["action"] == "create":
                if not mine:
                    await call({"type": "input_boolean/create", "name": req["canary"]})
                await call({"type": "config/core/update", "location_name": req["canary"]})
            helpers = await call({"type": "input_boolean/list"})
            mine = [h for h in helpers if h.get("name") == req["canary"]]
            # Reported as found, so the final verify checks the canary before deleting it.
            out["helpers"] = [h["name"] for h in mine]
            out["location"] = (await call({"type": "get_config"})).get("location_name", "")
            if req["action"] == "verify" and req.get("final"):
                for helper in mine:
                    await call({"type": "input_boolean/delete", "input_boolean_id": helper["id"]})
                await call({"type": "config/core/update", "location_name": DEFAULT_LOCATION})
    print(json.dumps(out))


try:
    asyncio.run(main())
except SystemExit as stop:
    print(json.dumps({"error": str(stop.code)}))
except Exception as failure:  # reported, not raised: see the header
    print(json.dumps({"error": "%s: %s" % (type(failure).__name__, failure)}))
