import asyncio
import json
import websockets  # online server
import asyncpg  # database (Supabase)


# Paste your Supabase connection string here.
# Keep this file private because the connection string contains a password.
databaseUrl = "postgresql://postgres.txbsllyawyvxqruaunul:JustDoodleYekiChannen!@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres"
pool = None

# A room holds the sockets for two players.
rooms = {}
playerRooms = {}
roomLock = asyncio.Lock()
nextRoomNumber = 1


async def send(player, data):
    try:
        await player.send(json.dumps(data))
    except websockets.exceptions.ConnectionClosed:
        pass


async def register(player, data):
    username = str(data.get("Username", "")).strip()
    password = str(data.get("Password", ""))

    if len(username) < 3 or len(username) > 20:
        message = "Username must be 3-20 characters."
    elif not username.replace("_", "a").isalnum():
        message = "Username can use letters, numbers, and _."
    elif len(password) < 8:
        message = "Password must contain at least 8 characters."
    else:
        try:
            await pool.execute(
                "INSERT INTO checkers_accounts (username, password) VALUES ($1, $2)",
                username,
                password,
            )
            await send(player, {
                "Action": "RegisterResult",
                "Success": True,
                "Message": "Account created. You can log in now.",
            })
            return
        except asyncpg.UniqueViolationError:
            message = "That username is already taken."
        except Exception as error:
            print("Registration error:", error)
            message = "Could not create account. Try again."

    await send(player, {"Action": "RegisterResult", "Success": False, "Message": message})


async def login(player, data):
    username = str(data.get("Username", "")).strip()
    password = str(data.get("Password", ""))
    user = await pool.fetchrow(
        "SELECT password FROM checkers_accounts WHERE username = $1",
        username,
    )

    if user is None or user["password"] != password:
        await send(player, {
            "Action": "LoginResult",
            "Success": False,
            "Message": "Wrong username or password.",
        })
        return None

    await send(player, {
        "Action": "LoginResult",
        "Success": True,
        "Username": username,
        "Message": f"Welcome, {username}!",
    })
    return username


async def findGame(player):
    global nextRoomNumber

    async with roomLock:
        if player in playerRooms:
            await send(player, {"Action": "Error", "Message": "You are already waiting or playing."})
            return

        for roomId, players in rooms.items():
            if len(players) == 1:
                players.append(player)
                playerRooms[player] = roomId
                await send(players[0], {"Action": "StartGame", "Turn": 0})
                await send(player, {"Action": "StartGame", "Turn": 1})
                return

        roomId = str(nextRoomNumber)
        nextRoomNumber += 1
        rooms[roomId] = [player]
        playerRooms[player] = roomId
        await send(player, {"Action": "WaitingForPlayer", "Room": roomId})


async def removePlayer(player):
    async with roomLock:
        roomId = playerRooms.pop(player, None)
        if roomId is None:
            return

        players = rooms.get(roomId, [])
        if player in players:
            players.remove(player)

        for opponent in players:
            await send(opponent, {"Action": "OpponentDisconnected"})

        if not players:
            rooms.pop(roomId, None)


async def handlePlayer(player):
    username = None

    try:
        async for message in player:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue

            action = data.get("Action")

            if action == "Register":
                await register(player, data)
            elif action == "Login":
                username = await login(player, data)
            elif username is None:
                await send(player, {
                    "Action": "Error",
                    "Message": "Log in before playing online.",
                })
            elif action == "FindGame":
                await findGame(player)
            elif action == "Move" or action == "Chat":
                roomId = playerRooms.get(player)
                if roomId:
                    for opponent in rooms.get(roomId, []):
                        if opponent != player:
                            await send(opponent, data)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await removePlayer(player)


async def main():
    global pool

    if databaseUrl == "PASTE_SUPABASE_DATABASE_URL_HERE":
        raise RuntimeError("Paste your Supabase connection string into databaseUrl first.")

    pool = await asyncpg.create_pool(databaseUrl, statement_cache_size=0)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS checkers_accounts (
            username VARCHAR(20) PRIMARY KEY,
            password TEXT NOT NULL
        )
    """)

    print("Checkers server running on port 10000")
    async with websockets.serve(handlePlayer, "0.0.0.0", 10000):
        await asyncio.Future()


asyncio.run(main())